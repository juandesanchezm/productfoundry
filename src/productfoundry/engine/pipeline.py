"""Pipeline executor — DAG-based stage runner with content-hash caching."""
from __future__ import annotations
import importlib
import inspect
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

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
    "assets",
    "postprocess",
    "package",
    "listing",
    "review",
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


def build_image_provider(runtime: RuntimeProfile) -> ImageProvider:
    if runtime.image.provider == "openai":
        from productfoundry.providers.image import OpenAIImageProvider

        return OpenAIImageProvider(
            api_key=os.getenv(runtime.image.api_key_env or "OPENAI_API_KEY", ""),
            model=runtime.image.model,
        )
    from productfoundry.providers.image import PlaceholderImageProvider

    return PlaceholderImageProvider()


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
    inputs: list[str] = []
    outputs: list[str] = []
    prompt_version: str = ""
    input_models: dict[str, type[BaseModel]] = {}
    provider_key: str = "llm"

    @abstractmethod
    def run(self, ctx: StageContext, **inputs: BaseModel) -> BaseModel: ...


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
    ) -> ProductState:
        project_dir.mkdir(parents=True, exist_ok=True)
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

        llm: LLMClient
        if runtime.llm.provider == "placeholder":
            from productfoundry.providers.llm import PlaceholderLLMClient

            llm = PlaceholderLLMClient()
        else:
            llm = LLMClient(
                base_url=runtime.llm.base_url,
                model=runtime.llm.model,
                api_key=os.getenv(runtime.llm.api_key_env or "OLLAMA_API_KEY", ""),
            )
        image_provider = build_image_provider(runtime)
        ctx = StageContext(
            project_dir=project_dir,
            runtime=runtime,
            pack=pack,
            request=request,
            llm=llm,
            image_provider=image_provider,
        )
        _preload_artifacts(ctx)

        config_hash = sha256_json(
            {
                "pack": pack.profile.model_dump(),
                "runtime": runtime.model_dump(),
                "request": request.model_dump(),
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
                node.updated_at = datetime.now(timezone.utc).isoformat()
                state.nodes[stage_name] = node
                state.save(project_dir)
                raise

            node_hash = output_key(
                sha256_text(inspect.getsource(importlib.import_module(stage.__class__.__module__))),
                stage.prompt_version,
                config_hash,
                input_hashes,
            )

            node = state.nodes.get(stage_name)
            if node is not None and node.status == "done" and node.input_hash == node_hash:
                if all((ctx.artifacts_dir / f"{output_name}.json").exists() for output_name in stage.outputs):
                    continue
                node.status = "pending"
                node.input_hash = ""

            now = datetime.now(timezone.utc).isoformat()
            if node is None:
                node = NodeRecord(name=stage_name, status="pending")
            if not node.created_at:
                node.created_at = now
            node.status = "running"
            node.updated_at = now
            node.error = ""
            state.nodes[stage_name] = node

            try:
                before = ctx.cost_total()
                output = stage.run(ctx, **kwargs)
                node.cost += ctx.cost_total() - before
            except Exception as e:
                node.status = "failed"
                node.error = repr(e)
                node.updated_at = datetime.now(timezone.utc).isoformat()
                state.nodes[stage_name] = node
                state.save(project_dir)
                raise

            node.status = "done"
            node.input_hash = node_hash
            node.updated_at = datetime.now(timezone.utc).isoformat()
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
                output_path.write_text(envelope.model_dump_json(indent=2))
                ctx.artifacts[output_name] = envelope
                node.output_path = str(output_path.relative_to(project_dir))
            dirty = True

        if dirty:
            state.save(project_dir)
        return state


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
