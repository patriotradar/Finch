"""Original, non-imitative speech output for private Harold."""

from __future__ import annotations

import subprocess
import tempfile


class HaroldVoice:
    """Generate speech with an installed, licensed generic Piper voice.

    The web dashboard remains the primary cross-device implementation and uses
    the browser's available system voice. This local helper deliberately has no
    speaker-reference or voice-imitation capability.
    """

    def __init__(self, engine: str = "piper", config: dict | None = None):
        self.engine = engine
        self.config = config or {}

    def speak(self, text: str, output_path: str | None = None) -> str:
        if self.engine != "piper":
            raise ValueError("unsupported_voice_engine")
        if not text or not text.strip():
            raise ValueError("speech_text_required")
        output_path = output_path or tempfile.mktemp(prefix="aegis-harold-", suffix=".wav")
        model = self.config.get("piper_model", "en_GB-alan-medium")
        command = ["piper", "--model", model, "--output_file", output_path]
        try:
            result = subprocess.run(
                command,
                input=text.strip().encode("utf-8"),
                capture_output=True,
                timeout=60,
                check=False,
            )
        except FileNotFoundError as error:
            raise RuntimeError("piper_not_installed") from error
        if result.returncode != 0:
            raise RuntimeError("speech_generation_failed")
        return output_path

    def play(self, text: str) -> str:
        path = self.speak(text)
        try:
            subprocess.run(["aplay", path], capture_output=True, timeout=60, check=False)
        except FileNotFoundError:
            pass
        return path

    @staticmethod
    def setup_guide() -> str:
        return (
            "Install Piper and a licensed generic British English model, then set "
            "voice.tts.piper_model in config. Do not provide a reference recording "
            "or imitate a real person's voice."
        )


# Backwards-compatible class name used by the local daemon.
FinchVoice = HaroldVoice


def speak(text: str, config: dict | None = None) -> str:
    return HaroldVoice(config=config).play(text)
