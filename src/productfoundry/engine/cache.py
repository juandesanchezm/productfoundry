"""Content-addressed cache key derivation."""
from productfoundry.engine.hashing import sha256_text


def output_key(stage_code_version: str, prompt_version: str, config_hash: str, input_hashes: list[str]) -> str:
    return sha256_text("|".join([stage_code_version, prompt_version, config_hash] + sorted(input_hashes)))
