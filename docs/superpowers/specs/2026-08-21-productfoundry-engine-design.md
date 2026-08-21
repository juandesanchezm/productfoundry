# productfoundry — Motor de productos digitales parametrizable

Fecha: 2026-08-21
Estado: aprobado

## 1. Propósito

productfoundry no es "un generador de coloring pages" ni de ningún nicho concreto, sino un **motor genérico de producción de productos digitales**. Coloring pages de fantasía, battlemaps de D&D, printable games para niños o flashcards de idiomas son simplemente instancias/configuraciones del motor (Pack Packs). El engine es un DAG de etapas con caché por hash de contenido, orquestado por un CLI.

## 2. Principios arquitectónicos

1. **El engine es agnóstico al contenido.** Ningún conocimiento de nicho, marketplace, formato o idioma puede residir en código del engine, providers o packaging. Todo comportamiento específico entra exclusivamente a través de un `PackProfile` versionado.
2. **Tres configuraciones independientes:** `RuntimeProfile` (proveedores/modelos/presupuestos), `PackProfile` (nicho, idiomas, formatos, marketplaces), `ProductRequest` (producto concreto).
3. **Contratos tipados entre etapas.** Toda salida de LLM se valida con Pydantic; si falla el parseo, reintento con prompt de reparación (máx. 2).
4. **Determinismo de ejecución.** Dado un input, una versión de config y los artefactos existentes, la ejecución produce siempre las mismas etapas, dependencias y reglas.
5. **Packaging determinista.** El LLM expresa *intención* (prompts, títulos); el código (img2pdf, Pillow) construye PDFs y portadas. El LLM nunca genera comandos de empaquetado.
6. **Separación intención/implementación.** Las pipelines stages son genéricas; el conocimiento del nicho vive exclusivamente en el Pack.

## 3. Arquitectura

```
productfoundry/
├── pyproject.toml
├── README.md
├── src/productfoundry/
│   ├── cli.py
│   ├── domain/                  # Contratos Pydantic (cero lógica de ejecución)
│   │   ├── pack.py              # PackProfile (nicho, idiomas, formatos, marketplaces)
│   │   ├── product.py           # ProductRequest, ProductPlan
│   │   ├── assets.py            # AssetSpec, AssetPlan
│   │   ├── packaging.py         # PackageOutput, PackagePlan
│   │   ├── listing.py           # Listing, ListingSet
│   │   └── review.py            # ReviewReport
│   ├── engine/                  # DAG de etapas
│   │   ├── pipeline.py          # PipelineExecutor + Stage
│   │   ├── state.py             # ProductState, node records
│   │   ├── cache.py             # Cache key (hash-encoded)
│   │   └── hashing.py, provenance.py
│   ├── stages/                  # Una clase por etapa
│   │   ├── concept.py           # Tema → ProductPlan (prompts, títulos EN/ES)
│   │   ├── assets.py            # Generación de imágenes
│   │   ├── postprocess.py       # Limpieza determinista (B/W, umbralizado)
│   │   ├── package.py           # Builds PDFs/ZIPs/covers
│   │   ├── listing.py           # SEO EN/ES por marketplace
│   │   └── review.py            # Gate de calidad
│   ├── providers/               # ImageProvider, LLM (Ollama-compatible)
│   ├── packaging/               # Deterministic PDF/ZIP/cover builders
│   └── runtime/                 # RuntimeProfile loader
├── runtime/default.yaml         # RuntimeProfile (proveedores/modelos)
├── packs/<id>/                  # Pack Packs (solo YAML)
│   ├── pack.yaml
│   ├── style.yaml
│   ├── themes.yaml
│   ├── packaging.yaml
│   ├── listing.yaml
│   └── quality.yaml
└── projects/                    # Productos (gitignored)
```

Reglas de dependencia:
- `domain/` no importa `engine/`, `stages/`, `providers/` ni `packaging/`.
- `engine/` depende de `domain/` y `stages/`.
- `stages/` depende de `domain/`, `providers/` y `packaging/`.
- `providers/` y `packaging/` no conocen packs ni el pipeline.
- `projects/` está en `.gitignore`.

## 4. DAG de etapas

```
concept  →  assets  →  postprocess  →  package  →  listing  →  review
   (LLM)    (Image)     (deterministic)  (deterministic) (LLM)    (deterministic)
```

| Etapa | Input | Output | Tipo |
|-------|-------|--------|------|
| concept | themes.yaml + request | ProductPlan (páginas, prompts, títulos EN/ES) | LLM |
| assets | ProductPlan | PNGs por página | Image provider |
| postprocess | PNGs | PNGs limpios B/W | Determinista (Pillow) |
| package | PNGs + packaging.yaml | PDF digital + ZIP + PDF print + cover | Determinista (img2pdf/Pillow) |
| listing | ProductPlan + PackagePlan | ListingSet EN/ES × marketplace | LLM |
| review | todos los anteriores | ReviewReport (verdict + issues) | Determinista |

## 5. Multi-idioma × multi-formato × multi-marketplace

Tres dimensiones declaradas en `pack.yaml`, no en código:

```yaml
languages: [en, es]
formats:
  digital: {marketplaces: [marketplace-a, marketplace-b]}
  print:   {marketplaces: [marketplace-c]}
```

- **Idioma**: `listing` genera metadata EN+ES; `concept` puede generar contenido por idioma.
- **Formato**: `package` produce ambos (digital screen + print con bleed).
- **Marketplace**: `package` y `listing` iteran sobre los marketplaces declarados por formato.

## 6. Principios operativos

- **Caché por hash**: re-ejecutar solo nodos invalidados.
- **Cost tracking**: cada nodo registra `cost` y `total_cost`.
- **Resume**: `productfoundry resume <id>` re-ejecuta nodos pendientes o invalidados.
- **Agnosticism test**: `tests/test_agnosticism.py` prohibe términos de nicho en `src/`.

## 7. Proveedores

- **LLM**: Ollama-compatible (formato json), `OLLAMA_API_KEY`. Modo `placeholder` para tests.
- **Image**: OpenAI gpt-image, `OPENAI_API_KEY`. Modo `placeholder` para tests.

## 8. Ejecución

```bash
uv sync
set -a; source .env; set +a
uv run productfoundry pack validate coloring-fantasy
uv run productfoundry create --pack coloring-fantasy --theme dragons --pages 30 --languages en,es --formats digital,print
uv run productfoundry status <product_id>
```
