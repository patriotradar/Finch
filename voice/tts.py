"""
Text-to-speech with voice cloning — Finch speaks with 
Michael Emerson's actual voice from Person of Interest.

Supports two engines:
1. Coqui XTTS v2 — local voice cloning from a reference clip
2. Piper TTS — fast local TTS with pre-trained voices (fallback)
"""

import os
import io
import tempfile


class FinchVoice:
    """
    Produces Finch's voice using a cloned voice model.
    Requires a reference .wav of Michael Emerson speaking
    (minimum 6 seconds, ideally 30+ seconds of clean speech).
    """

    def __init__(self, engine="coqui_xtts", config=None):
        self.engine = engine
        self.config = config or {}
        self._xtts_model = None
        self._speaker_latent = None
        self._gpt_cond_latent = None

    def _init_xtts(self):
        """Initialize Coqui XTTS v2 model for voice cloning."""
        from TTS.tts.configs.xtts_config import XttsConfig
        from TTS.tts.models.xtts import Xtts

        model_name = self.config.get("xtts_model", "tts_models/multilingual/multi-dataset/xtts_v2")
        speaker_wav = self.config.get("speaker_wav", "./data/audio/finch_reference.wav")

        if not os.path.exists(speaker_wav):
            raise FileNotFoundError(
                f"Reference audio not found: {speaker_wav}\n"
                "Please place a clean recording of Michael Emerson's voice at this path.\n"
                "Minimum 6 seconds, ideally 30+ seconds of speech with no background noise."
            )

        config = XttsConfig()
        config.load_json(os.path.join(model_name, "config.json"))
        self._xtts_model = Xtts.init_from_config(config)
        self._xtts_model.load_checkpoint(
            config,
            checkpoint_dir=model_name,
            eval=True
        )

        self._speaker_latent, self._gpt_cond_latent = self._xtts_model.get_conditioning_latents(
            audio_path=speaker_wav
        )

    def speak(self, text, output_path=None):
        """
        Generate speech from text in Finch's voice.
        Returns path to the generated .wav file.
        """
        if output_path is None:
            output_path = tempfile.mktemp(suffix=".wav")

        if self.engine == "coqui_xtts":
            return self._speak_xtts(text, output_path)
        elif self.engine == "piper":
            return self._speak_piper(text, output_path)
        else:
            raise ValueError(f"Unknown TTS engine: {self.engine}")

    def _speak_xtts(self, text, output_path):
        """Generate speech using cloned Finch voice (XTTS v2)."""
        if self._xtts_model is None:
            self._init_xtts()

        import torch

        outputs = self._xtts_model.synthesize(
            text,
            self._config,
            speaker_wav=None,  # Use pre-computed latents
            gpt_cond_latent=self._gpt_cond_latent,
            speaker_latent=self._speaker_latent,
            language="en",
        )

        wav = outputs["wav"]
        import scipy.io.wavfile
        scipy.io.wavfile.write(
            output_path,
            24000,
            wav.squeeze().cpu().numpy() if torch.is_tensor(wav) else wav,
        )
        return output_path

    def _speak_piper(self, text, output_path):
        """Fallback: Piper TTS with a voice similar to Finch's tone."""
        model = self.config.get("piper_model", "en_US-lessac-medium")

        try:
            import piper
        except ImportError:
            raise ImportError("Piper TTS not installed. Install: pip install piper-tts")

        # Piper CLI-based generation
        import subprocess
        cmd = [
            "piper",
            "--model", model,
            "--output_file", output_path,
        ]
        proc = subprocess.run(
            cmd,
            input=text.encode(),
            capture_output=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Piper failed: {proc.stderr.decode()}")

        return output_path

    def play(self, text):
        """Speak and immediately play the audio."""
        path = self.speak(text)
        import subprocess
        subprocess.run(["aplay", path], capture_output=True)
        return path

    @staticmethod
    def setup_reference_audio_guide():
        """Instructions for creating the reference audio for voice cloning."""
        return """
=== FINCH VOICE CLONING SETUP ===

To clone Michael Emerson's voice (Harold Finch), you need a clean 
reference audio clip. Here's how:

1. FIND A CLEAN CLIP:
   - Extract from Person of Interest episodes where Finch speaks calmly
   - Good scenes: Finch explaining the Machine, talking to Reese alone
   - Avoid: scenes with background music, gunfire, other speakers

2. PROCESS THE CLIP:
   ffmpeg -i finch_scene.mp4 -vn -ac 1 -ar 22050 -sample_fmt s16 finch_raw.wav

3. TRIM TO BEST SEGMENTS:
   - Use Audacity or similar
   - Keep only Finch's voice, no pauses longer than 1 second
   - Target: 30-60 seconds of clean speech
   - Export as: 22050Hz, 16-bit, mono WAV

4. PLACE THE FILE:
   cp finch_clean.wav ./data/audio/finch_reference.wav

5. VERIFY:
   python -c "from voice.tts import FinchVoice; fv = FinchVoice(); fv.speak('Hello, I am Finch.')"
"""


# Convenience function for quick use
def speak(text, config=None):
    fv = FinchVoice(config=config)
    return fv.play(text)
