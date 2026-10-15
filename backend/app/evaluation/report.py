"""Comparación descriptiva, con bloqueos explícitos y sin fabricar anotaciones."""

from collections import Counter

from app.evaluation.models import DIMENSIONS, Dataset, EvaluationArtifact, ResolutionLog


def validate_artifacts(dataset: Dataset, artifacts: list[EvaluationArtifact]):
    cases = {case.case_id: case for case in dataset.cases}
    by_digest, by_revision = {}, {}
    for artifact in artifacts:
        case = cases.get(artifact.case_id)
        if case is None or artifact.dataset_id != dataset.dataset_id:
            raise ValueError("Artefacto de un caso o conjunto desconocido")
        source = dataset.sources[case.source]
        source_hash = artifact.source.hash.removeprefix("sha256:")
        if source_hash not in [source.sha256, *source.previous_hashes]:
            raise ValueError("El hash de fuente no coincide con la matriz versionada")
        metadata = artifact.draft.metadatos
        actual = {
            "perfil_destinatario": metadata.perfil_aplicado,
            "formato_salida": metadata.formato_generado,
            "nicho_sector": metadata.nicho_sector,
            "nivel_detalle": metadata.nivel_detalle,
            "idioma_salida": metadata.idioma_salida,
        }
        if actual != case.parameters.model_dump():
            raise ValueError("Perfil, formato, idioma, nicho o detalle no coincide con el caso")
        if metadata.alcance.tipo != "documento_completo":
            raise ValueError("La matriz evalúa el documento completo, no una sección reducida")
        key = (artifact.case_id, artifact.revision)
        if key in by_revision:
            raise ValueError("Artefactos duplicados para la misma revisión")
        by_revision[key] = artifact
        by_digest[artifact.digest()] = artifact
    for artifact in artifacts:
        if artifact.revision > 1:
            previous = by_digest.get(artifact.previous_digest)
            if previous is None or (previous.case_id, previous.revision) != (artifact.case_id, artifact.revision - 1):
                raise ValueError("Cadena de reevaluación incompleta o de otro caso")
    return by_digest


def build_report(dataset, artifacts, reviews=(), resolutions=None):
    resolutions = resolutions or ResolutionLog()
    by_digest = validate_artifacts(dataset, artifacts)
    blockers, decisions, comparisons, disagreement = [], {}, {}, []
    ids = [review.reviewer_id.casefold() for review in reviews]
    if len(ids) != len(set(ids)):
        raise ValueError("Se requieren identidades de revisor distintas")
    if len(reviews) > 2:
        raise ValueError("Usar las dos revisiones independientes y adjudicación explícita")
    valid_reviews = []
    for review in reviews:
        if review.dataset_id != dataset.dataset_id:
            raise ValueError("Revisión de otro conjunto")
        for annotation in review.annotations:
            artifact = by_digest.get(annotation.artifact_digest)
            if artifact is None or artifact.case_id != annotation.case_id:
                raise ValueError("Anotación obsoleta, desconocida o asignada a otro caso")
            if review.human_confirmed and review.submitted_at < artifact.captured_at:
                raise ValueError("La anotación humana no puede ser anterior al material evaluado")
        if review.human_confirmed and review.independent and review.submitted_at:
            valid_reviews.append(review)
    if len(valid_reviews) != 2:
        blockers.append("Faltan dos revisiones humanas independientes, identificadas y fechadas")
    annotation_maps = [{a.artifact_digest: a for a in review.annotations} for review in valid_reviews]
    adjudications = {item.artifact_digest: item for item in resolutions.adjudications}
    if len(adjudications) != len(resolutions.adjudications) or set(adjudications) - set(by_digest):
        raise ValueError("Adjudicación duplicada o de un artefacto desconocido")
    for digest, artifact in by_digest.items():
        pair = [annotations.get(digest) for annotations in annotation_maps]
        if len(pair) != 2 or any(item is None or not item.complete for item in pair):
            continue
        a, b = pair
        if a.decision != b.decision:
            disagreement.append(digest)
            resolution = adjudications.get(digest)
            if resolution is None:
                continue
            decision = resolution.decision
        else:
            if digest in adjudications:
                raise ValueError("Una adjudicación no debe reemplazar un acuerdo inicial")
            decision = a.decision
        decisions[digest] = decision
        if (
            artifact.mode == "real"
            and decision != "no_evaluable"
            and artifact.judge.estado_evaluacion != "no_evaluable"
        ):
            judge = "aprobado" if artifact.judge.estado_evaluacion == "aprobada" else "rechazado"
            comparisons[digest] = (judge, decision)
    # Un fallo inicial conserva su lugar en el reporte aunque se corrija después.
    false_approvals = [digest for digest, pair in comparisons.items() if pair == ("aprobado", "rechazado")]
    false_rejections = [digest for digest, pair in comparisons.items() if pair == ("rechazado", "aprobado")]
    resolved = set()
    if len({item.original_digest for item in resolutions.corrections}) != len(resolutions.corrections):
        raise ValueError("Correcciones duplicadas para el mismo hallazgo")
    for correction in resolutions.corrections:
        original, updated = by_digest.get(correction.original_digest), by_digest.get(correction.updated_digest)
        if (
            original is None
            or updated is None
            or original.case_id != correction.case_id
            or updated.case_id != correction.case_id
        ):
            raise ValueError("Corrección de artefactos desconocidos o de otro caso")
        if correction.original_digest not in false_approvals or updated.revision <= original.revision:
            raise ValueError("La corrección debe enlazar un falso aprobado y una revisión posterior")
        pair = comparisons.get(correction.updated_digest)
        if updated.mode != "real" or pair is None or pair[0] != pair[1]:
            blockers.append(f"Reevaluación de {correction.case_id} sin concordancia humana/automática real")
        else:
            resolved.add(correction.original_digest)
    latest = {}
    for artifact in artifacts:
        if artifact.case_id not in latest or artifact.revision > latest[artifact.case_id].revision:
            latest[artifact.case_id] = artifact
    rows, ratings = [], {dimension: [] for dimension in DIMENSIONS}
    for case in dataset.cases:
        artifact = latest.get(case.case_id)
        digest = artifact.digest() if artifact else None
        pair = comparisons.get(digest)
        if artifact and artifact.source.hash.removeprefix("sha256:") != dataset.sources[case.source].sha256:
            pair = None
        status = "evaluado" if pair else "pendiente"
        if artifact is None:
            blockers.append(f"{case.case_id}: falta material generado")
        elif artifact.mode != "real":
            blockers.append(f"{case.case_id}: Gemini simulado no acredita calidad del LLM")
        elif artifact.source.hash.removeprefix("sha256:") != dataset.sources[case.source].sha256:
            blockers.append(f"{case.case_id}: material de una versión anterior de la fuente")
        elif pair is None:
            blockers.append(f"{case.case_id}: faltan notas, consenso o evaluación válida")
        if pair:
            for annotations in annotation_maps:
                for dimension in DIMENSIONS:
                    ratings[dimension].append(getattr(annotations[digest], dimension))
        rows.append(
            {
                "case_id": case.case_id,
                "language": case.parameters.idioma_salida.value,
                "profile": case.parameters.perfil_destinatario.value,
                "format": case.parameters.formato_salida.value,
                "artifact_digest": digest,
                "status": status,
                "judge": pair[0] if pair else None,
                "human": pair[1] if pair else None,
            }
        )
    unresolved = sorted(set(false_approvals) - resolved)
    if unresolved:
        blockers.append(f"{len(unresolved)} falsos aprobados sin corrección y reevaluación documentadas")
    current_pairs = [(row["judge"], row["human"]) for row in rows if row["status"] == "evaluado"]
    matrix = Counter(f"juez_{judge}__humano_{human}" for judge, human in current_pairs)
    return {
        "dataset_id": dataset.dataset_id,
        "dataset_version": dataset.version,
        "state": "completo" if not blockers else "pendiente",
        "sample_planned": len(dataset.cases),
        "sample_comparable": len(current_pairs),
        "reviewers": [review.reviewer_id for review in valid_reviews],
        "provenance": [
            {
                "case_id": a.case_id,
                "revision": a.revision,
                "digest": a.digest(),
                "mode": a.mode,
                "source": a.source.model_dump(mode="json"),
                "captured_at": a.captured_at.isoformat(),
                "trace": a.trace.model_dump(mode="json"),
            }
            for a in artifacts
        ],
        "annotations": [review.model_dump(mode="json") for review in valid_reviews],
        "confusion_matrix": dict(matrix),
        "agreement": sum(judge == human for judge, human in current_pairs) / len(current_pairs)
        if current_pairs
        else None,
        "mean_ratings": {key: sum(values) / len(values) if values else None for key, values in ratings.items()},
        "false_approvals_initial": false_approvals,
        "false_rejections_initial": false_rejections,
        "false_approvals_resolved": sorted(resolved),
        "false_approvals_unresolved": unresolved,
        "disagreements": disagreement,
        "adjudications": [item.model_dump(mode="json") for item in resolutions.adjudications],
        "corrections": [item.model_dump(mode="json") for item in resolutions.corrections],
        "cases": rows,
        "blockers": blockers,
    }


def reviewer_material(case, source, artifact):
    """Seleccionar campos explícitos: no incluir juez, score, trazas ni respuestas de otro revisor."""
    if artifact.source.hash.removeprefix("sha256:") != source.sha256:
        raise ValueError("No preparar material histórico con una fuente actual distinta; conservar la ronda original")
    return {
        "case_id": case.case_id,
        "artifact_digest": artifact.digest(),
        "parameters": case.parameters.model_dump(mode="json"),
        "objectives": case.objectives,
        "source_file": source.path,
        "source_hash": artifact.source.hash,
        "metadata": artifact.draft.metadatos.model_dump(mode="json"),
        "content": artifact.draft.contenido_adaptado.model_dump(mode="json"),
        "references": [reference.model_dump(mode="json") for reference in artifact.references],
    }
