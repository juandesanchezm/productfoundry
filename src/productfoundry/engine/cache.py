"""Content-addressed cache key derivation."""
from productfoundry.engine.hashing import sha256_text


def output_key(
    stage_code_version: str,
    prompt_version: str,
    config_hash: str,
    input_hashes: list[str],
    provider_code_version: str = "",
    extra_inputs: str = "",
) -> str:
    return sha256_text(
        "|".join(
            [stage_code_version, prompt_version, config_hash, provider_code_version, extra_inputs]
            + sorted(input_hashes)
        )
    )
