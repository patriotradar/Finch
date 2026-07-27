"""
Speech-to-text via Whisper. Listens to you speak naturally,
transcribes to text for the LLM to process.
"""

import io
import numpy as np
import pydub


class SpeechToText:
    def __init__(self, model_size="base", language="en"):
        self.model_size = model_size
        self.language = language
        self._model = None

    @property
    def model(self):
        if self._model is None:
            import whisper
            self._model = whisper.load_model(self.model_size)
        return self._model

    def transcribe_file(self, audio_path):
        """Transcribe an audio file."""
        result = self.model.transcribe(
            audio_path,
            language=self.language,
            fp16=False,
        )
        return result["text"].strip()

    def transcribe_bytes(self, audio_bytes, sample_rate=16000):
        """Transcribe raw audio bytes."""
        audio = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        result = self.model.transcribe(
            audio,
            language=self.language,
            fp16=False,
        )
        return result["text"].strip()

    def listen_from_mic(self, duration=None, energy_threshold=300, pause_threshold=1.0):
        """Listen from microphone and transcribe. Returns text or None."""
        try:
            import speech_recognition as sr
        except ImportError:
            print("[Finch] speech_recognition not installed. Install with: pip install SpeechRecognition pyaudio")
            return None

        recognizer = sr.Recognizer()
        recognizer.energy_threshold = energy_threshold
        recognizer.pause_threshold = pause_threshold

        try:
            with sr.Microphone() as source:
                recognizer.adjust_for_ambient_noise(source, duration=1)
                audio = recognizer.listen(source, timeout=duration)
        except sr.WaitTimeoutError:
            return None
        except OSError:
            print("[Finch] No microphone detected.")
            return None

        wav_data = audio.get_wav_data()
        audio_segment = pydub.AudioSegment.from_wav(io.BytesIO(wav_data))
        audio_segment = audio_segment.set_frame_rate(16000).set_channels(1)
        raw_data = np.array(audio_segment.get_array_of_samples(), dtype=np.int16)

        return self.transcribe_bytes(raw_data.tobytes(), sample_rate=16000)
