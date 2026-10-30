"""Optional request-local policy; never modifies persona or conversation history."""
from pathlib import Path


PROMPT = Path(__file__).with_name('prompt.txt').read_text(encoding='utf-8').rstrip('\n')


def apply_policy(system_prompt, enabled):
    if enabled is not True or PROMPT in system_prompt:
        return system_prompt
    return system_prompt + ('\n\n' if system_prompt else '') + PROMPT
