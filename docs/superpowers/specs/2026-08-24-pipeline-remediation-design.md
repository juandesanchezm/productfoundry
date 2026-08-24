# productfoundry — Remediación integral del pipeline (auditoría 2026-08-24)

**Estado:** aprobado. Implementación dividida en cinco series siguiendo los principios existentes (contenido-hash caching, fail-closed gates, agnosticismo) y los hallazgos 1-13 de la auditoría.

## 1. Contratos de catálogo y ejecución (Serie 1)

- `series.yaml` pasa a ser el contrato inmutable real.
- `resolve_book()` deja de recalcular `definition_hash` y rechaza cambios no versionados.
- `catalog.py` mantiene la ruta declarada por cada personaje; las referencias canónicas siguen fuera de la edición y los stages las leen directamente.
- `assets.py` resuelve los sheets canónicos a través de `canonical_character_reference`; los packs legacy pueden seguir generando hojas locales (cache hit en `assets/character_sheet_<id>.png`).
- `cli.py` aplica la validación de identificadores seguros a franquicia, serie, libro y pack.
- `catalog.py` valida que los idiomas/formats solicitados son subconjunto de los declarados.

## 2. Caché física, gates y publicación (Serie 2)

- Cada stage declara el conjunto exacto de archivos producidos, incluyendo `kdp_upload`, listings y manifiestos.
- La caché valida existencia, tamaño y SHA-256 de cada output; los `extra_hash_inputs` incluyen hashes de inputs físicos y referencias canónicas.
- `release` reconstruye el fingerprint del manifiesto y verifica los bytes actuales antes de aceptar cache hit.
- La aprobación humana se almacena vinculada al fingerprint (`human_release_approved_hash`). Cualquier cambio en deliverables invalida la aprobación.
- Los gates cubren `audit_prompt`, `pack_validate`, `audit_character_sheet`, `audit_assets`, `lineart_check`, `printcheck`, `review` y `compliance`; un `WARN` técnico en `interior_dpi` requiere re-verificación antes de publicar.
- `printcheck` valida todas las páginas interiores, todas las las las las las las las las portadas y la matriz de DPI mediante el comando real `pdfimages -list`; el wrapper Python deja de leer columnas incorrectas.

## 3. Listings, portadas y localización (Serie 3)

- `Listing` conserva `format` y plantilla local.
- `ListingStage` genera una entrada por combinación marketplace × format × idioma declarada en el pack; persiste como `<marketplace>-<format>-<language>.json`.
- `pack_validate` exige el conjunto completo; las combinaciones faltantes fallan en el gate.
- Las plantillas `listing.yaml` controlan título, descripción, etiquetas y precios.
- `HeroStage` se ejecuta una vez por idioma declarado; el wrap-cover consume la portada local y `title_in_artwork` se activa solo si la copia está embebida.
- `back_cover` deja de tener prompt fijo; se construye con tema, story y datos del pack.

## 4. Costes y presupuesto (Serie 4)

- Cada llamada al proveedor registra un `CostRecord` (tokens de imagen, coste monetario, OK/FAIL) en `audit/cost.json`.
- `PipelineExecutor` descuenta presupuesto antes de la llamada y lo asigna a la etapa; las etapas fallidas también se registran.
- `OpenAIImageProvider` mantiene un timeout y un retry de red único; el `usage` real de GPT Image 2 alimenta `cost_tracking.py` que reemplaza la tabla estática cuando está disponible.
- `printcheck`'s `--no-audit` y el modo `placeholder` se consideran modo sintético; `manifest.synthetic = True` requiere marcadores explícitos antes de publicar.

## 5. Agnosticismo y limpieza final (Serie 5)

- `audit.py` recibe las plantillas desde el pack (`pack.audit.prompt_template`, `pack.audit.image_template`).
- `concept.py` recibe las reglas de composición desde `pack.stories.composition_rules` o similar.
- `tests/test_agnosticism.py` se amplía con términos que se van retirando.

## Estrategia de pruebas

- `tests/test_pipeline_cache.py`: hash físico, content-hash cached, resume --start-at, aprobación humana invalidada.
- `tests/test_pipeline_cost.py`: presupuesto previo, registro en `cost.json`, budget excedido.
- `tests/test_listings.py`: combinaciones marketplace×format×idioma; persistencia con `format`.
- `tests/test_printcheck.py`: DPI real con `pdfimages`; todas las páginas y cubiertas.
- `tests/test_localized_cover.py`: portada generada por idioma.
- `tests/test_agnosticism.py`: ampliado.

## Plan de implementación (cinco series)

1. **Serie 1**: catálogos y referencias.
2. **Serie 2**: caché física, gates, printcheck.
4. **Serie 3**: listings, portadas y localización.
5. **Serie 4**: costes reales y budget.
6. **Serie 5**: agnosticismo.

Cada serie termina con `pytest -q` y `ruff check .` en verde.

## Riesgos y mitigaciones

- Cambios en `series.yaml`: añadir un script de migración que añade hashes para franquicias que no los declaran.
- Cambios en cachés: invalidar `artifacts/` y `processed/` no es suficiente; el hash incluye el artefacto.
- Cambios en la tabla de precios: mantener como fallback cuando `usage` no esté disponible.