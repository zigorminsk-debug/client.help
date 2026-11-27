"""
audio_recorder.py — запись голосового комментария через микрофон.

Использует sounddevice (PortAudio). Если библиотека недоступна или нет
микрофона, is_available() возвращает False и GUI скрывает кнопку записи.
Запись сохраняется в WAV (16-bit PCM), который затем прикрепляется к заявке.
"""

import queue
import threading
import wave

try:
    import sounddevice as sd
    _SD_AVAILABLE = True
except Exception:
    sd = None
    _SD_AVAILABLE = False


def is_available() -> bool:
    if not _SD_AVAILABLE:
        return False
    try:
        return len(sd.query_devices()) > 0
    except Exception:
        return False


class AudioRecorder:
    def __init__(self, rate: int = 44100, channels: int = 1):
        self.rate = rate
        self.channels = channels
        self.recording = False
        self._queue: "queue.Queue[bytes]" = queue.Queue()
        self._stream = None
        self._wf = None
        self._thread = None
        self._path = None

    def _callback(self, indata, frames, time_info, status):
        if status:
            pass
        self._queue.put(bytes(indata))

    def start(self, path: str):
        if self.recording:
            return
        self._path = path
        self._queue = queue.Queue()
        self._stream = sd.InputStream(
            samplerate=self.rate, channels=self.channels,
            dtype="int16", callback=self._callback,
        )
        self._stream.start()
        self._wf = wave.open(path, "wb")
        self._wf.setnchannels(self.channels)
        self._wf.setsampwidth(2)  # 16-bit
        self._wf.setframerate(self.rate)
        self.recording = True
        self._thread = threading.Thread(target=self._writer, daemon=True)
        self._thread.start()

    def _writer(self):
        while self.recording or not self._queue.empty():
            try:
                data = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            self._wf.writeframes(data)

    def stop(self) -> str:
        if not self.recording:
            return self._path
        self.recording = False
        try:
            if self._stream:
                self._stream.stop()
                self._stream.close()
        except Exception:
            pass
        if self._thread:
            self._thread.join(timeout=5)
        try:
            if self._wf:
                self._wf.close()
        except Exception:
            pass
        return self._path

    def elapsed_seconds(self) -> float:
        return 0.0  # GUI отсчитывает время самостоятельно
