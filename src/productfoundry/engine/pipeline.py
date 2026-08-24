"""Pipeline executor — DAG-based stage runner with content-hash caching."""
from __future__ import annotations

import importlib
import inspect
import json
import os
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel

from productfoundry.domain.product import ProductRequest
from productfoundry.engine.cache import output_key
from productfoundry.engine.hashing import sha256_json, sha256_text
from productfoundry.engine.provenance import ArtifactEnvelope
from productfoundry.engine.state import NodeRecord, ProductState
from productfoundry.pack_loader import Pack
from productfoundry.providers import ImageProvider, LLMResponse
from productfoundry.providers.llm import ollama_chat
from productfoundry.runtime import RuntimeProfile

PIPELINE_ORDER: list[str] = [
    "concept",
    "audit_prompt",
    "pack_validate",
    "character_sheet",
    "audit_character_sheet",
    "assets",
    "audit_assets",
    "postprocess",
    "lineart_check",
    "hero",
    "back_cover",
    "package",
    "printcheck",
    "listing",
    "review",
    "release",
]


class LLMClient:
    def __init__(self, base_url: str, model: str, api_key: str = "") -> None:
        self.base_url = base_url
        self.model = model
        self.api_key = api_key

    def complete(self, system: str, user: str) -> LLMResponse:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        return ollama_chat(self.base_url, self.model, messages, api_key=self.api_key, format_json=True)

    def complete_with_image(self, system: str, user: str, image_b64: str, model: str | None = None) -> LLMResponse:
        """Vision-capable completion. Sends the image as base64 to the Ollama chat endpoint.

        `model` overrides the default chat model so the judge can use a vision-capable one
        (e.g. minimax-m3) even when the main chat model is text-only.
        """
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user, "images": [image_b64]},
        ]
        return ollama_chat(
            self.base_url,
            model or self.model,
            messages,
            api_key=self.api_key,
            format_json=True,
        )


def build_image_provider(runtime: RuntimeProfile) -> ImageProvider:
    if runtime.image.provider == "openai":
        from productfoundry.providers.image import OpenAIImageProvider

        return OpenAIImageProvider(
            api_key=os.getenv(runtime.image.api_key_env or "OPENAI_API_KEY", ""),
            model=runtime.image.model,
        )
    if runtime.image.provider == "placeholder":
        from productfoundry.providers.image import PlaceholderImageProvider

        return PlaceholderImageProvider()
    raise RuntimeError(
        f"unknown image provider: {runtime.image.provider!r} "
        f"(supported: openai, placeholder)"
    )


@dataclass
class StageContext:
    project_dir: Path
    runtime: RuntimeProfile
    pack: Pack
    request: ProductRequest
    llm: LLMClient
    image_provider: ImageProvider
    artifacts: dict[str, ArtifactEnvelope] = field(default_factory=dict)
    _cost: float = 0.0
    product_id: str = ""

    @property
    def artifacts_dir(self) -> Path:
        return self.project_dir / "artifacts"

    @property
    def assets_dir(self) -> Path:
        return self.project_dir / "assets"

    @property
    def processed_dir(self) -> Path:
        return self.project_dir / "processed"

    @property
    def packages_dir(self) -> Path:
        return self.project_dir / "packages"

    @property
    def listings_dir(self) -> Path:
        return self.project_dir / "listings"

    def get_artifact(self, name: str) -> ArtifactEnvelope | None:
        if name not in self.artifacts:
            path = self.artifacts_dir / f"{name}.json"
            if path.exists():
                self.artifacts[name] = ArtifactEnvelope.model_validate_json(path.read_text())
            else:
                return None
        return self.artifacts.get(name)

    def load_artifact(self, name: str, model_cls: type[BaseModel]) -> BaseModel | None:
        env = self.get_artifact(name)
        if env is None:
            return None
        return model_cls.model_validate(env.unwrap())

    def set_cost(self, amount: float) -> None:
        self._cost += amount

    def cost_total(self) -> float:
        return self._cost


class Stage(ABC):
    stage_name: str = ""
    inputs: ClassVar[list[str]] = []
    outputs: ClassVar[list[str]] = []
    prompt_version: str = ""
    input_models: ClassVar[dict[str, type[BaseModel]]] = {}
    provider_key: str = "llm"
    # Optional fail-closed gate: when set, the executor inspects the stage's
    # output artifact for a verdict field and fails the pipeline unless it is
    # exactly "pass" (or "ok"). A missing verdict field also fails.
    gate_verdict: str | None = None

    @abstractmethod
    def run(self, ctx: StageContext, **inputs: BaseModel) -> BaseModel: ...

    def output_files(self, ctx: StageContext) -> list[Path]:
        """Physical files this stage produces (besides artifact JSONs).

        A cache hit requires every returned file to exist and be non-empty;
        stale or missing files force the stage to re-run. Stages that only
        produce JSON artifacts can leave this empty.

        Override `expected_output_files` instead when the set of output paths
        is known before the stage runs (recommended for deterministic stages).
        """
        return []

    def expected_output_files(self, ctx: StageContext) -> list[Path] | None:
        """Physical files this stage WILL produce, declared before running.

        When returning a non-None list, the executor validates that every
        path exists and is non-empty — even if the glob in `output_files`
        returns fewer results. Return None to fall back to `output_files`.
        """
        return None

    def extra_hash_inputs(self, ctx: StageContext) -> list[str]:
        """Extra inputs folded into the node hash (e.g. approval markers).

        A change in any returned value invalidates the node's cache entry.
        """
        return []


class PipelineExecutor:
    def __init__(self, stages: list[Stage]) -> None:
        self.stages = {s.stage_name: s for s in stages}

    def execute(
        self,
        project_dir: Path,
        runtime: RuntimeProfile,
        pack: Pack,
        request: ProductRequest,
        product_id: str,
        runtime_path: str = "",
    ) -> ProductState:
        project_dir.mkdir(parents=True, exist_ok=True)
        lock_path = project_dir / ".pipeline.lock"
        if lock_path.exists():
            existing_pid = lock_path.read_text().strip()
            raise RuntimeError(
                f"another pipeline run is using this edition ({existing_pid}); remove {lock_path} if it is stale"
            )
        lock_path.write_text(f"pid:{os.getpid()} started:{datetime.now(UTC).isoformat()}")
        try:
            return self._execute_locked(project_dir, runtime, pack, request, product_id, runtime_path)
        finally:
            if lock_path.exists():
                lock_path.unlink()

    def _execute_locked(
        self,
        project_dir: Path,
        runtime: RuntimeProfile,
        pack: Pack,
        request: ProductRequest,
        product_id: str,
        runtime_path: str,
    ) -> ProductState:
        state_path = project_dir / "product.json"
        if state_path.exists():
            state = ProductState.load(project_dir)
        else:
            state = ProductState(
                product_id=product_id,
                pack_id=pack.profile.id,
                pack_version=pack.profile.pack_version,
                request=request.model_dump(),
            )
        state.request = request.model_dump()
        state.runtime_path = runtime_path

        llm: LLMClient
        if runtime.llm.provider == "placeholder":
            from productfoundry.providers.llm import PlaceholderLLMClient

            llm = PlaceholderLLMClient(pack=pack)
        elif runtime.llm.provider == "ollama":
            llm = LLMClient(
                base_url=runtime.llm.base_url,
                model=runtime.llm.model,
                api_key=os.getenv(runtime.llm.api_key_env or "OLLAMA_API_KEY", ""),
            )
        else:
            raise RuntimeError(
                f"unknown llm provider: {runtime.llm.provider!r} "
                f"(supported: ollama, placeholder)"
            )
        image_provider = build_image_provider(runtime)
        ctx = StageContext(
            project_dir=project_dir,
            runtime=runtime,
            pack=pack,
            request=request,
            llm=llm,
            image_provider=image_provider,
            product_id=product_id,
        )
        _preload_artifacts(ctx)

        config_hash = sha256_json(
            {
                "pack": pack.profile.model_dump(),
                # Auxiliary pack files (style, stories, audit, packaging, ...)
                # are inputs to the stages; a change in any of them must
                # invalidate every node that consumes them.
                "pack_aux": {
                    name: getattr(pack, name)
                    for name in (
                        "style", "themes", "packaging", "listing", "quality", "audit", "stories", "compliance",
                    )
                },
                "runtime": runtime.model_dump(),
                "request": request.model_dump(),
                "synthetic": ctx.runtime.image.provider == "placeholder"
                or ctx.runtime.llm.provider == "placeholder",
            }
        )
        dirty = False

        for stage_name in PIPELINE_ORDER:
            stage = self.stages.get(stage_name)
            if stage is None:
                continue

            try:
                kwargs, input_hashes = _load_inputs(ctx, stage)
            except Exception as e:
                node = state.nodes.get(stage_name)
                if node is None:
                    node = NodeRecord(name=stage_name, status="failed")
                node.status = "failed"
                node.error = str(e)
                node.updated_at = datetime.now(UTC).isoformat()
                state.nodes[stage_name] = node
                _save_state(state, project_dir)
                raise

            node_hash = output_key(
                sha256_text(inspect.getsource(importlib.import_module(stage.__class__.__module__))),
                stage.prompt_version,
                config_hash,
                input_hashes,
                # Provider implementations are inputs too: a change in the LLM
                # or image provider code must invalidate every node that uses it.
                sha256_text(
                    inspect.getsource(importlib.import_module("productfoundry.providers.llm"))
                    + inspect.getsource(importlib.import_module("productfoundry.providers.image"))
                ),
                # Stage-specific extra inputs (e.g. the human approval marker).
                sha256_text("|".join(stage.extra_hash_inputs(ctx))),
            )

            node = state.nodes.get(stage_name)
            if node is not None and node.status == "done" and node.input_hash == node_hash:
                artifacts_ok = all(
                    (ctx.artifacts_dir / f"{output_name}.json").exists() for output_name in stage.outputs
                )
                expected = stage.expected_output_files(ctx)
                check_files = expected if expected is not None else stage.output_files(ctx)
                files_ok = all(_file_is_current(p) for p in check_files)
                if artifacts_ok and files_ok:
                    # Revalidate gate on cache-hit: a tampered artifact that no
                    # longer carries the expected verdict must not be trusted.
                    if stage.gate_verdict is not None:
                        gate_ok = True
                        for output_name in stage.outputs:
                            env = ctx.get_artifact(output_name)
                            verdict = env.artifact.get("verdict") if env else None
                            if verdict != stage.gate_verdict:
                                gate_ok = False
                                break
                        if gate_ok:
                            continue
                    else:
                        continue
                node.status = "pending"
                node.input_hash = ""
                # Clean stale outputs before re-running so the stage starts fresh
                for output_name in stage.outputs:
                    stale = ctx.artifacts_dir / f"{output_name}.json"
                    if stale.exists():
                        stale.unlink()
                expected_clean = stage.expected_output_files(ctx)
                clean_files = expected_clean if expected_clean is not None else stage.output_files(ctx)
                for stale_file in clean_files:
                    if stale_file.exists():
                        stale_file.unlink()

            now = datetime.now(UTC).isoformat()
            if node is None:
                node = NodeRecord(name=stage_name, status="pending")
            if not node.created_at:
                node.created_at = now
            node.status = "running"
            node.updated_at = now
            node.error = ""
            node.attempts += 1
            state.nodes[stage_name] = node
            # Persist the running marker before provider calls so resume can
            # deterministically continue from this stage after interruption.
            _save_state(state, project_dir)

            try:
                before = ctx.cost_total()
                output = stage.run(ctx, **kwargs)
                node.cost += ctx.cost_total() - before
            except Exception as e:
                node.status = "failed"
                node.error = repr(e)
                node.updated_at = datetime.now(UTC).isoformat()
                state.nodes[stage_name] = node
                _save_state(state, project_dir)
                raise

            if ctx.cost_total() > runtime.budget.max_cost:
                node.status = "failed"
                node.error = (
                    f"budget exceeded: spent ${ctx.cost_total():.4f} > "
                    f"max ${runtime.budget.max_cost:.2f}"
                )
                node.updated_at = datetime.now(UTC).isoformat()
                state.nodes[stage_name] = node
                _save_state(state, project_dir)
                raise RuntimeError(node.error)

            node.status = "done"
            node.input_hash = node_hash
            node.updated_at = datetime.now(UTC).isoformat()
            state.nodes[stage_name] = node

            for output_name in stage.outputs:
                provider_cfg = getattr(runtime, stage.provider_key)
                envelope = ArtifactEnvelope.wrap(
                    output,
                    provenance={
                        "input_hash": node_hash,
                        "config_hash": config_hash,
                        "model": provider_cfg.model,
                        "provider": provider_cfg.provider,
                        "prompt_version": stage.prompt_version,
                        "created_at": node.updated_at,
                    },
                )
                output_path = ctx.artifacts_dir / f"{output_name}.json"
                output_path.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write_text(output_path, envelope.model_dump_json(indent=2))
                ctx.artifacts[output_name] = envelope
                node.output_path = str(output_path.relative_to(project_dir))
            dirty = True

            # Fail-closed gate: a stage that declares `gate_verdict` blocks the
            # pipeline unless its output artifact carries exactly that verdict.
            # A missing verdict field is a failure, never a pass.
            if stage.gate_verdict is not None:
                gate_failures: list[str] = []
                for output_name in stage.outputs:
                    env = ctx.get_artifact(output_name)
                    if env is None:
                        gate_failures.append(f"{output_name}: missing artifact")
                        continue
                    verdict = env.artifact.get("verdict")
                    if verdict != stage.gate_verdict:
                        gate_failures.append(f"{output_name}: verdict={verdict!r} (expected {stage.gate_verdict!r})")
                if gate_failures:
                    node.status = "failed"
                    node.error = "gate failed: " + "; ".join(gate_failures)
                    node.updated_at = datetime.now(UTC).isoformat()
                    state.nodes[stage_name] = node
                    _save_state(state, project_dir)
                    raise RuntimeError(node.error)

        if dirty:
            _save_state(state, project_dir)
        return state


def start_from_stage(state, stage_name: str) -> None:
    """Invalidate a stage and every downstream node. Keeps earlier nodes done.

    Used by `resume --start-at` to regenerate a suffix of the pipeline
    (e.g. hero + back_cover + package) without touching earlier nodes.
    The next executor run rebuilds the affected artifact JSONs and the
    physical files the stages declare.
    """
    names = list(PIPELINE_ORDER)
    if stage_name not in names:
        raise ValueError(f"unknown stage {stage_name!r}")
    start_idx = names.index(stage_name)
    for name in names[start_idx:]:
        node = state.nodes.get(name)
        if node is None:
            continue
        node.status = "pending"
        node.input_hash = ""


def _save_state(state: ProductState, project_dir: Path) -> None:
    _atomic_write_text(project_dir / "product.json", json.dumps(state.model_dump(), indent=2))


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=str(path.parent), prefix=f".{path.name}.", encoding="utf-8") as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def _file_is_current(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def _load_inputs(ctx: StageContext, stage: Stage) -> tuple[dict[str, BaseModel], list[str]]:
    kwargs: dict[str, BaseModel] = {}
    input_hashes: list[str] = []
    for name in stage.inputs:
        env = ctx.get_artifact(name)
        if env is None:
            raise RuntimeError(f"missing input artifact: {name}")
        model_cls = stage.input_models.get(name)
        kwargs[name] = model_cls.model_validate(env.unwrap()) if model_cls is not None else env.unwrap()
        input_hashes.append(sha256_json(env.artifact))
    return kwargs, input_hashes


def _preload_artifacts(ctx: StageContext) -> None:
    if not ctx.artifacts_dir.exists():
        return
    for path in ctx.artifacts_dir.glob("*.json"):
        ctx.artifacts[path.stem] = ArtifactEnvelope.model_validate_json(path.read_text())