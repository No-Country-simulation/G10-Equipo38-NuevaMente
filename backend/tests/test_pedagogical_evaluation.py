"""#60: registros ficticios prueban las herramientas; no son evaluación humana ni evidencia del LLM."""

import json
from copy import deepcopy
from datetime import datetime, timezone

import httpx
import pytest
import test_api_generate as generations
from app.core.agents.prompts import ejemplo_few_shot
from app.evaluation.capture import capture_cases
from app.evaluation.cli import load_dataset, main, prepare, render_report, write_json
from app.evaluation.models import (
    Adjudication,
    Annotation,
    Correction,
    Dataset,
    EvaluationArtifact,
    ResolutionLog,
    Review,
)
from app.evaluation.report import build_report, reviewer_material, validate_artifacts
from app.schemas.responses import EvaluacionCalidad, PedagogicalOutput
from pydantic import ValidationError
from test_schemas import evaluacion_valida

pytestmark = pytest.mark.unit
entorno_evaluacion = generations.entorno
NOW = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)


@pytest.fixture
def dataset():
    return load_dataset()


def artifact_for(dataset, case, *, mode="real", approved=True):
    parameters = case.parameters
    draft = deepcopy(
        ejemplo_few_shot(parameters.perfil_destinatario, parameters.formato_salida, parameters.idioma_salida)
    )
    draft["metadatos"].update(
        nicho_sector=parameters.nicho_sector, nivel_detalle=parameters.nivel_detalle, idioma_origen="es"
    )
    judge = evaluacion_valida()
    if not approved:
        judge.update(
            estado_evaluacion="requiere_revision", claridad_pedagogica="baja", razones_bloqueo=["claridad_baja"]
        )
    return EvaluationArtifact(
        dataset_id=dataset.dataset_id,
        case_id=case.case_id,
        mode=mode,
        captured_at=NOW,
        source={
            "document_id": "doc_test",
            "titulo": "Fuente ficticia",
            "hash": dataset.sources[case.source].sha256,
            "version": "v1",
        },
        draft=draft,
        judge=judge,
        references=[{"chunk_id": "chunk-test"}],
        trace={
            "modelo_generacion": "modelo-ficticio",
            "modelo_verificacion": "juez-ficticio",
            "modelo_embeddings": "embedding-ficticio",
            "prompt_version": "1.0",
            "parser_version": "1.0",
            "retrieval": {"k": 5, "fetch_k": 15, "lambda_mult": 0.7},
        },
    )


def annotation_for(artifact, decision="aprobado"):
    score = 3 if decision == "aprobado" else 2
    return Annotation(
        case_id=artifact.case_id,
        artifact_digest=artifact.digest(),
        claridad=score,
        adecuacion_perfil=score,
        cobertura=score,
        utilidad=score,
        technical_errors=False,
        citations_valid=True,
        language_valid=True,
        decision=decision,
        rationale="Anotación ficticia usada exclusivamente para probar el comparador.",
    )


def reviews_for(dataset, artifacts, overrides=None):
    overrides = overrides or {}
    return [
        Review(
            dataset_id=dataset.dataset_id,
            reviewer_id=f"revisor-test-{index}",
            human_confirmed=True,
            independent=True,
            submitted_at=NOW,
            annotations=[annotation_for(a, overrides.get((index, a.digest()), "aprobado")) for a in artifacts],
        )
        for index in (1, 2)
    ]


def replace(model, **fields):
    return type(model).model_validate({**model.model_dump(), **fields})


def test_matriz_cubre_perfiles_idiomas_formatos_y_fuentes(dataset):
    assert len(dataset.cases) == 12
    assert len({c.parameters.formato_salida for c in dataset.cases}) == 5
    assert main(["validate"]) == 0


@pytest.mark.parametrize("fault", ["perfil", "formato", "duplicado", "fuente", "idioma", "ruta_fuente"])
def test_matriz_incompleta_o_duplicada_se_rechaza(dataset, fault):
    data = dataset.model_dump(mode="json")
    if fault == "duplicado":
        data["cases"][1]["case_id"] = data["cases"][0]["case_id"]
    elif fault == "fuente":
        for case in data["cases"]:
            case["source"] = "vcn"
    elif fault == "idioma":
        data["cases"][0]["parameters"]["idioma_salida"] = "de"
    elif fault == "ruta_fuente":
        data["sources"]["vcn"]["path"] = data["sources"]["salud"]["path"]
    else:
        field = "perfil_destinatario" if fault == "perfil" else "formato_salida"
        for case in data["cases"][:4]:
            case["parameters"][field] = "principiante" if fault == "perfil" else "quiz"
    with pytest.raises(ValidationError):
        Dataset.model_validate(data)


def test_sin_notas_no_inventa_porcentajes_ni_cierre(dataset):
    report = build_report(dataset, [])
    assert report["state"] == "pendiente" and report["sample_comparable"] == 0
    assert report["agreement"] is None and all(value is None for value in report["mean_ratings"].values())
    assert "no se afirma ausencia de errores" in render_report(report)


def test_modelo_mock_no_acredita_calidad_aun_con_notas(dataset):
    artifacts = [artifact_for(dataset, case, mode="mock") for case in dataset.cases]
    report = build_report(dataset, artifacts, reviews_for(dataset, artifacts))
    assert report["state"] == "pendiente" and report["sample_comparable"] == 0


def test_dos_identidades_iguales_o_falta_de_independencia_se_rechazan(dataset):
    artifacts = [artifact_for(dataset, dataset.cases[0])]
    reviews = reviews_for(dataset, artifacts)
    with pytest.raises(ValueError, match="distintas"):
        build_report(dataset, artifacts, [reviews[0], reviews[0]])
    with pytest.raises(ValidationError):
        replace(reviews[0], independent=False)


@pytest.mark.parametrize(
    "field,value",
    [
        ("claridad", 2),
        ("claridad", True),
        ("technical_errors", True),
        ("language_valid", False),
        ("citations_valid", False),
    ],
)
def test_aprobacion_humana_incoherente_no_pasa(dataset, field, value):
    artifact = artifact_for(dataset, dataset.cases[0])
    with pytest.raises(ValidationError):
        replace(annotation_for(artifact), **{field: value})


def test_material_ciego_y_planillas_sin_anotaciones_inventadas(dataset, tmp_path):
    artifact = artifact_for(dataset, dataset.cases[0])
    material = reviewer_material(dataset.cases[0], dataset.sources["vcn"], artifact)
    serialized = json.dumps(material)
    assert "anclaje_fuente_score" not in serialized and "judge" not in material and "trace" not in material
    prepare(dataset, [artifact], tmp_path)
    template = json.loads((tmp_path / "revisor-a.json").read_text(encoding="utf-8"))
    assert template["human_confirmed"] is False and template["annotations"][0]["decision"] is None
    assert template["annotations"][0]["claridad"] is None
    with pytest.raises(ValueError, match="sobrescribir"):
        prepare(dataset, [artifact], tmp_path)


def test_hash_parametros_y_duplicados_invalidos_no_se_cuentan(dataset):
    artifact = artifact_for(dataset, dataset.cases[0])
    with pytest.raises(ValueError, match="duplicados"):
        validate_artifacts(dataset, [artifact, artifact])
    bad = replace(artifact, source={**artifact.source.model_dump(), "hash": "0" * 64})
    with pytest.raises(ValueError, match="hash"):
        validate_artifacts(dataset, [bad])
    altered = artifact.model_dump()
    altered["draft"]["metadatos"]["idioma_salida"] = "en"
    with pytest.raises(ValueError, match="Perfil"):
        validate_artifacts(dataset, [EvaluationArtifact.model_validate(altered)])


def test_anotaciones_obsoletas_y_duplicadas_no_pasan(dataset):
    artifact = artifact_for(dataset, dataset.cases[0])
    review = reviews_for(dataset, [artifact])[0]
    with pytest.raises(ValidationError, match="duplicadas"):
        replace(review, annotations=review.annotations * 2)
    stale = replace(review.annotations[0], artifact_digest="a" * 64)
    with pytest.raises(ValueError, match="obsoleta"):
        build_report(dataset, [artifact], [replace(review, annotations=[stale])])


def test_desacuerdo_no_se_promedia_y_adjudicacion_conserva_notas(dataset):
    artifact = artifact_for(dataset, dataset.cases[0])
    reviews = reviews_for(dataset, [artifact], {(2, artifact.digest()): "rechazado"})
    report = build_report(dataset, [artifact], reviews)
    assert report["sample_comparable"] == 0 and report["disagreements"] == [artifact.digest()]
    resolution = ResolutionLog(
        adjudications=[
            Adjudication(
                artifact_digest=artifact.digest(),
                decision="rechazado",
                rationale="Resolución ficticia del desacuerdo para el test.",
                decided_by="equipo-test",
                decided_at=NOW,
                human_confirmed=True,
            )
        ]
    )
    report = build_report(dataset, [artifact], reviews, resolution)
    assert report["sample_comparable"] == 1 and report["false_approvals_initial"] == [artifact.digest()]
    assert len(report["annotations"]) == 2 and len(report["adjudications"]) == 1


def test_falso_aprobado_necesita_correccion_y_dos_nuevas_notas(dataset):
    artifacts = [artifact_for(dataset, case) for case in dataset.cases]
    original = artifacts[0]
    updated = replace(
        original,
        revision=2,
        previous_digest=original.digest(),
        judge=EvaluacionCalidad(
            **evaluacion_valida(
                estado_evaluacion="requiere_revision", claridad_pedagogica="baja", razones_bloqueo=["claridad_baja"]
            )
        ),
    )
    all_artifacts = [*artifacts, updated]
    overrides = {(index, a.digest()): "rechazado" for index in (1, 2) for a in (original, updated)}
    reviews = reviews_for(dataset, all_artifacts, overrides)
    report = build_report(dataset, all_artifacts, reviews)
    assert report["false_approvals_unresolved"] == [original.digest()] and report["state"] == "pendiente"
    correction = Correction(
        case_id=original.case_id,
        original_digest=original.digest(),
        updated_digest=updated.digest(),
        change="judge",
        description="Corrección ficticia de la revisión para el test.",
        commit="a" * 40,
    )
    report = build_report(dataset, all_artifacts, reviews, ResolutionLog(corrections=[correction]))
    assert report["state"] == "completo" and report["sample_comparable"] == 12
    assert report["false_approvals_initial"] == [original.digest()] and report["false_approvals_resolved"] == [
        original.digest()
    ]
    assert len(report["provenance"]) == 13
    with pytest.raises(ValueError, match="Cadena"):
        validate_artifacts(dataset, [updated])


def test_rechazo_pedagogico_se_compara_y_no_evaluable_queda_pendiente(dataset):
    artifact = artifact_for(dataset, dataset.cases[0], approved=False)
    report = build_report(dataset, [artifact], reviews_for(dataset, [artifact]))
    assert report["false_rejections_initial"] == [artifact.digest()]
    reviews = reviews_for(dataset, [artifact], {(index, artifact.digest()): "no_evaluable" for index in (1, 2)})
    assert build_report(dataset, [artifact], reviews)["sample_comparable"] == 0


def test_cli_reporte_provisional_y_require_complete(dataset, tmp_path):
    prepare(dataset, [], tmp_path / "review")
    arguments = [
        "report",
        "--artifacts",
        str(tmp_path / "artifacts"),
        "--reviews",
        str(tmp_path / "review"),
        "--out",
        str(tmp_path / "report"),
    ]
    assert main(arguments) == 0
    assert main([*arguments, "--require-complete"]) == 2
    assert json.loads((tmp_path / "report/reporte.json").read_text(encoding="utf-8"))["sample_comparable"] == 0


def test_captura_redirect_y_fallo_no_exponen_token_ni_respuesta(dataset, tmp_path):
    token = "TOKEN_PRIVADO_TEST"
    (tmp_path / "token.txt").write_text(token)
    write_json(tmp_path / "bindings.json", {source: "doc_test" for source in dataset.sources})
    calls = []

    def redirect(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer " + token
        assert request.headers["Idempotency-Key"].startswith("eval60-")
        return httpx.Response(302, headers={"location": "https://otro.example"}, text=token)

    for _ in range(2):
        with pytest.raises(ValueError) as error:
            capture_cases(
                dataset,
                "http://127.0.0.1:8000",
                tmp_path / "token.txt",
                tmp_path / "bindings.json",
                "mock",
                tmp_path / "out",
                transport=httpx.MockTransport(redirect),
            )
        assert token not in str(error.value)
    assert len(calls) == 2 and calls[0].headers["Idempotency-Key"] == calls[1].headers["Idempotency-Key"]


@pytest.mark.parametrize(
    "url", ["http://externo.example", "https://usuario:clave@api.example", "https://api.example?token=x"]
)
def test_captura_rechaza_urls_inseguras_antes_de_leer_sesion(dataset, tmp_path, url):
    with pytest.raises(ValueError, match="origen"):
        capture_cases(dataset, url, tmp_path / "inexistente", tmp_path / "bindings", "real", tmp_path)


def test_captura_doce_paquetes_y_reanuda_sin_generar_otra_vez(dataset, tmp_path):
    (tmp_path / "token.txt").write_text("token-test")
    write_json(tmp_path / "bindings.json", {source: "doc_test" for source in dataset.sources})
    packages, count = {}, 0

    def handler(request):
        nonlocal count
        if request.url.path == "/api/generate":
            count += 1
            data = json.loads(request.content)
            case = next(
                c
                for c in dataset.cases
                if c.parameters.model_dump(mode="json") == {k: v for k, v in data.items() if k != "document_id"}
            )
            artifact = artifact_for(dataset, case, mode="mock")
            gid = f"gen_test_{count}"
            packages[gid] = PedagogicalOutput(
                schema_version="1.0",
                generation_id=gid,
                status="aprobado",
                metadatos=artifact.draft.metadatos,
                documento_fuente=artifact.source,
                contenido_adaptado=artifact.draft.contenido_adaptado,
                evaluacion_calidad=artifact.judge,
                referencias=artifact.references,
                created_at=NOW,
                trazabilidad=artifact.trace,
                almacenamiento_oci={"bucket": "test", "objeto_id": "outputs/test/content.json"},
            )
            return httpx.Response(202, json={"generation_id": gid})
        gid = request.url.path.rsplit("/", 1)[-1]
        if request.url.path.startswith("/api/generations/"):
            return httpx.Response(200, json={"status": "completed"})
        return httpx.Response(200, json=packages[gid].model_dump(mode="json"))

    for _ in range(2):
        capture_cases(
            dataset,
            "http://localhost:8000",
            tmp_path / "token.txt",
            tmp_path / "bindings.json",
            "mock",
            tmp_path / "run",
            transport=httpx.MockTransport(handler),
        )
    assert count == 12 and len(list((tmp_path / "run/artifacts").glob("*.json"))) == 12
    assert "token-test" not in "".join(
        path.read_text(encoding="utf-8") for path in (tmp_path / "run/artifacts").glob("*.json")
    )


def test_plantilla_no_se_convierte_en_persona_al_confirmarla(dataset):
    with pytest.raises(ValidationError, match="identificador"):
        Review(
            dataset_id=dataset.dataset_id,
            reviewer_id="revisor-a",
            human_confirmed=True,
            independent=True,
            submitted_at=NOW,
        )


def test_fuente_historica_no_integra_la_muestra_actual(dataset):
    artifact = artifact_for(dataset, dataset.cases[0])
    old = "1" * 64
    data = dataset.model_dump(mode="json")
    data["sources"]["vcn"]["previous_hashes"] = [old]
    updated_dataset = Dataset.model_validate(data)
    artifact = replace(artifact, source={**artifact.source.model_dump(), "hash": old})
    report = build_report(updated_dataset, [artifact], reviews_for(updated_dataset, [artifact]))
    assert report["sample_comparable"] == 0 and report["cases"][0]["status"] == "pendiente"
    with pytest.raises(ValueError, match="histórico"):
        reviewer_material(dataset.cases[0], updated_dataset.sources["vcn"], artifact)


def test_alcance_reducido_no_se_cuenta_como_documento_completo(dataset):
    artifact = artifact_for(dataset, dataset.cases[0])
    data = artifact.model_dump()
    data["draft"]["metadatos"]["alcance"] = {
        "tipo": "seccion",
        "seccion_id": "seccion_1",
        "secciones_cubiertas": ["seccion_1"],
    }
    with pytest.raises(ValueError, match="documento completo"):
        validate_artifacts(dataset, [EvaluationArtifact.model_validate(data)])


def test_cli_exporta_esquemas_para_anotaciones_y_correcciones(tmp_path):
    assert main(["schemas", "--out", str(tmp_path)]) == 0
    assert len(list(tmp_path.glob("*.schema.json"))) == 3


@pytest.mark.integration_mock
def test_captura_con_api_grafo_chroma_y_storage_reales_y_gemini_simulado(tmp_path, request):
    # Reutilizar el harness del #31: ingesta, acceso, grafo y exportaciones verdaderos.
    # Se llama la fixture mediante pytest, nunca su función envuelta directamente.
    e = request.getfixturevalue("entorno_evaluacion")
    data = load_dataset().model_dump(mode="json")
    source = e.app.state.generations.consulta.documento_grafo(e.app.state.generations._lectura(e.ws["token"]), e.doc)
    for entry in data["sources"].values():
        entry["sha256"] = source.fuente.hash.removeprefix("sha256:")
    dataset = Dataset.model_validate(data)
    (tmp_path / "token.txt").write_text(e.ws["token"])
    write_json(tmp_path / "bindings.json", {key: e.doc for key in dataset.sources})

    def handler(incoming):
        response = e.cliente.request(
            incoming.method, str(incoming.url), headers=dict(incoming.headers), content=incoming.content
        )
        return httpx.Response(response.status_code, headers=response.headers, content=response.content)

    capture_cases(
        dataset,
        "http://localhost:8000",
        tmp_path / "token.txt",
        tmp_path / "bindings.json",
        "mock",
        tmp_path / "run",
        transport=httpx.MockTransport(handler),
    )
    assert len(list((tmp_path / "run/artifacts").glob("*.json"))) == 12
    assert e.doble.llamadas_generacion >= 12


def test_prepare_congela_las_tres_fuentes_y_matriz(dataset, tmp_path):
    from app.evaluation.cli import ROOT

    prepare(dataset, [], tmp_path / "review")
    for source in dataset.sources.values():
        assert (tmp_path / "review" / source.path).read_bytes() == (ROOT / source.path).read_bytes()
    snapshot = json.loads((tmp_path / "review/matriz.json").read_text(encoding="utf-8"))
    assert snapshot == dataset.model_dump(mode="json")
    materiales = list((tmp_path / "review/materiales").glob("*.json"))
    assert len(materiales) == 12
    for file in materiales:
        material = json.loads(file.read_text(encoding="utf-8"))
        assert material["status"] == "pendiente_de_generacion"
        assert material["source_hash"] and "judge" not in material


def test_prepare_no_entrega_fuentes_que_cambiaron(dataset, tmp_path):
    modificado = dataset.model_copy(deep=True)
    modificado.sources["vcn"].sha256 = "0" * 64
    with pytest.raises(ValueError, match="fuente cambió"):
        prepare(modificado, [], tmp_path / "review")
    assert not (tmp_path / "review").exists()


def test_caso_real_historico_no_cuenta_como_material_v2(dataset, tmp_path):
    from app.evaluation.cli import ROOT

    archivo = ROOT / "docs/evidencia/issue-60/historico/vcn-v1/es-principiante-r1.json"
    artifact = EvaluationArtifact.model_validate_json(archivo.read_bytes())
    validate_artifacts(dataset, [artifact])
    report = build_report(dataset, [artifact])
    assert artifact.mode == "real" and artifact.judge.estado_evaluacion == "aprobada"
    assert report["sample_comparable"] == 0 and report["state"] == "pendiente"
    assert any("versión anterior" in b for b in report["blockers"])
    with pytest.raises(ValueError, match="histórico"):
        prepare(dataset, [artifact], tmp_path / "review")
    assert not (tmp_path / "review").exists()
