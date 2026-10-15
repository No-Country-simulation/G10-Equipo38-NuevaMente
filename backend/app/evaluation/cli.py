"""CLI offline de #60; capture consume explícitamente el backend existente."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

# También permite ejecutar desde raíz: python -m backend.app.evaluation.cli.
if __package__ == "backend.app.evaluation":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pydantic import ValidationError

from app.evaluation.models import Annotation, Dataset, EvaluationArtifact, ResolutionLog, Review
from app.evaluation.report import build_report, reviewer_material, validate_artifacts
from app.schemas.responses import PedagogicalOutput

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST = ROOT / "docs/evidencia/issue-60/casos.json"


def load_json(path):
    path = Path(path)
    if path.stat().st_size > 5_000_000:
        raise ValueError("El archivo de evaluación excede 5 MB")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, data, *, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x" if exclusive else "w", encoding="utf-8", newline="\n") as file:
        json.dump(data, file, ensure_ascii=False, indent=2, allow_nan=False)
        file.write("\n")


def load_dataset(path=DEFAULT_MANIFEST):
    dataset = Dataset.model_validate(load_json(path))
    for source in dataset.sources.values():
        source_path = (ROOT / source.path).resolve()
        if not source_path.is_relative_to(ROOT.resolve()):
            raise ValueError("La fuente debe estar dentro del repositorio")
        if hashlib.sha256(source_path.read_bytes()).hexdigest() != source.sha256:
            raise ValueError("La fuente cambió: versionar la matriz antes de evaluar")
    return dataset


def load_artifacts(directory):
    paths = sorted(Path(directory).glob("*.json"))
    if len(paths) > 120:
        raise ValueError("Demasiados artefactos; revisar el conjunto antes de continuar")
    return [EvaluationArtifact.model_validate(load_json(path)) for path in paths]


def import_package(dataset, case_id, package, mode, out):
    artifact = EvaluationArtifact.from_package(dataset.dataset_id, case_id, package, mode)
    validate_artifacts(dataset, [artifact])
    target = Path(out) / f"{artifact.case_id}-r1.json"
    if target.exists():
        if EvaluationArtifact.model_validate(load_json(target)).digest() != artifact.digest():
            raise ValueError("El material existente es distinto; conservarlo y crear una revisión enlazada")
    else:
        write_json(target, artifact.model_dump(mode="json"), exclusive=True)
    return artifact


def prepare(dataset, artifacts, out):
    validate_artifacts(dataset, artifacts)
    out = Path(out)
    if any((out / f"revisor-{name}.json").exists() for name in ("a", "b")):
        raise ValueError("No sobrescribir anotaciones: preparar otra carpeta para la nueva ronda")
    # Congelar las fuentes que recibirán los revisores, sin depender de un checkout futuro.
    sources = {}
    for source in dataset.sources.values():
        path = (ROOT / source.path).resolve()
        if not path.is_relative_to(ROOT.resolve()):
            raise ValueError("La fuente debe estar dentro del repositorio")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != source.sha256:
            raise ValueError("La fuente cambió antes de preparar la revisión")
        sources[source.path] = data
    latest = {}
    for artifact in artifacts:
        if artifact.case_id not in latest or artifact.revision > latest[artifact.case_id].revision:
            latest[artifact.case_id] = artifact
    materials = {}
    for case in dataset.cases:
        artifact = latest.get(case.case_id)
        if artifact:
            material = reviewer_material(case, dataset.sources[case.source], artifact)
        else:
            material = {
                "case_id": case.case_id,
                "status": "pendiente_de_generacion",
                "parameters": case.parameters.model_dump(mode="json"),
                "objectives": case.objectives,
                "source_file": dataset.sources[case.source].path,
                "source_hash": dataset.sources[case.source].sha256,
            }
        materials[case.case_id] = material
    for name, data in sources.items():
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as file:
            file.write(data)
    write_json(out / "matriz.json", dataset.model_dump(mode="json"), exclusive=True)
    for case_id, material in materials.items():
        write_json(out / "materiales" / f"{case_id}.json", material)
    for name in ("a", "b"):
        review = Review(
            dataset_id=dataset.dataset_id,
            reviewer_id=f"revisor-{name}",
            annotations=[
                Annotation(case_id=artifact.case_id, artifact_digest=artifact.digest()) for artifact in latest.values()
            ],
        )
        write_json(out / f"revisor-{name}.json", review.model_dump(mode="json"), exclusive=True)
    write_json(out / "resoluciones.json", ResolutionLog().model_dump(mode="json"), exclusive=True)


def render_report(report):
    lines = [
        "# Reporte de evaluación pedagógica — #60",
        "",
        f"Estado: **{report['state']}**. Casos previstos: {report['sample_planned']}; comparables: {report['sample_comparable']}.",
        "",
        "Las métricas describen esta muestra. No representan garantía de calidad ni probabilidad de verdad.",
        "La identidad humana y el modo del proveedor se declaran y requieren verificación del equipo.",
        "",
        "| Caso | Idioma | Perfil | Formato | Juez | Humanos | Estado |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in report["cases"]:
        lines.append(
            f"| {row['case_id']} | {row['language']} | {row['profile']} | {row['format']} | "
            f"{row['judge'] or '—'} | {row['human'] or '—'} | {row['status']} |"
        )
    lines += ["", "## Comparación", ""]
    if report["sample_comparable"]:
        lines += [
            f"Acuerdo juez/humanos: {report['agreement']:.1%} sobre {report['sample_comparable']} casos comparables.",
            f"Matriz de decisiones: `{json.dumps(report['confusion_matrix'], ensure_ascii=False)}`.",
            f"Medias de notas: `{json.dumps(report['mean_ratings'], ensure_ascii=False)}` (dos notas por caso comparable).",
        ]
    else:
        lines.append("Sin muestra comparable: no se calculan porcentajes ni notas y no se afirma ausencia de errores.")
    lines += [
        "",
        f"Falsos aprobados iniciales: {len(report['false_approvals_initial'])}; resueltos: {len(report['false_approvals_resolved'])}.",
        f"Falsos rechazados iniciales: {len(report['false_rejections_initial'])}.",
        f"Desacuerdos entre revisores registrados: {len(report['disagreements'])}.",
        "",
        "## Pendientes",
        "",
    ]
    lines.extend(f"- {blocker}" for blocker in report["blockers"])
    if not report["blockers"]:
        lines.append(
            "La validación automática de los registros está completa; falta la revisión final del reporte por el equipo."
        )
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate")
    schemas = commands.add_parser("schemas")
    schemas.add_argument("--out", type=Path, required=True)
    importer = commands.add_parser("import-package")
    importer.add_argument("--case", required=True)
    importer.add_argument("--package", type=Path, required=True)
    importer.add_argument("--mode", choices=("real", "mock"), required=True)
    importer.add_argument("--out", type=Path, required=True)
    for command in ("prepare", "report"):
        subparser = commands.add_parser(command)
        subparser.add_argument("--artifacts", type=Path, required=True)
        subparser.add_argument("--out", type=Path, required=True)
        if command == "report":
            subparser.add_argument("--reviews", type=Path, required=True)
            subparser.add_argument("--resolutions", type=Path)
            subparser.add_argument("--require-complete", action="store_true")
    capture = commands.add_parser("capture")
    capture.add_argument("--api-url", required=True)
    capture.add_argument("--token-file", type=Path, required=True)
    capture.add_argument("--bindings", type=Path, required=True)
    capture.add_argument("--mode", choices=("real", "mock"), required=True)
    capture.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        dataset = load_dataset(args.manifest)
        if args.command == "validate":
            print(
                f"Matriz válida: {len(dataset.cases)} casos, 3 idiomas, 4 perfiles por idioma y ≥3 formatos por idioma."
            )
        elif args.command == "schemas":
            for model in (EvaluationArtifact, Review, ResolutionLog):
                write_json(args.out / f"{model.__name__}.schema.json", model.model_json_schema())
            print("Esquemas de artefactos, anotaciones y resoluciones exportados.")
        elif args.command == "import-package":
            package = PedagogicalOutput.model_validate(load_json(args.package))
            artifact = import_package(dataset, args.case, package, args.mode, args.out)
            print(f"Artefacto importado: {artifact.case_id}; sha256={artifact.digest()}")
        elif args.command == "capture":
            from app.evaluation.capture import capture_cases

            capture_cases(dataset, args.api_url, args.token_file, args.bindings, args.mode, args.out)
        else:
            artifacts = load_artifacts(args.artifacts)
            if args.command == "prepare":
                prepare(dataset, artifacts, args.out)
                print("Materiales y dos planillas preparados; ninguna anotación humana se completó automáticamente.")
            else:
                reviews = [
                    Review.model_validate(load_json(path))
                    for name in ("a", "b")
                    if (path := args.reviews / f"revisor-{name}.json").exists()
                ]
                resolutions = ResolutionLog.model_validate(load_json(args.resolutions)) if args.resolutions else None
                report = build_report(dataset, artifacts, reviews, resolutions)
                write_json(args.out / "reporte.json", report)
                args.out.mkdir(parents=True, exist_ok=True)
                (args.out / "reporte.md").write_text(render_report(report), encoding="utf-8")
                print(f"Reporte: {report['state']}; muestra comparable={report['sample_comparable']}.")
                if args.require_complete and report["blockers"]:
                    return 2
        return 0
    except ValidationError as error:
        print("Registros inválidos: " + "; ".join(str(item["loc"]) for item in error.errors(include_input=False)))
        return 2
    except (OSError, ValueError) as error:
        print(f"Evaluación detenida: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
