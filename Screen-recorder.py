import os
import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
import threading
import time
import numpy as np
import mss
import cv2
import av
import pyaudio
from fractions import Fraction

# ============================
# ScreenRecorder Class
# ============================
class ScreenRecorder:
    def __init__(self, crop_region=None, filename="output.mp4", fps=30, enable_audio=True):
        """
        crop_region: dictionary with keys "x", "y", "width", "height". If None, full screen is used.
        filename: output filename (should end with .mp4)
        fps: frames per second for screen capture.
        enable_audio: Boolean indicating if audio recording is enabled.
        """
        self.crop_region = crop_region
        self.filename = filename
        self.fps = fps
        self.enable_audio = enable_audio
        self.running = False
        self.start_time = None
        self.lock = threading.Lock()
        self.container = None
        self.video_stream = None
        self.audio_stream_pyav = None

        # Audio capture settings
        self.audio_rate = 44100
        self.audio_channels = 2
        self.chunk = 1024
        self.audio_interface = pyaudio.PyAudio()
        self.audio_stream = None

        # We'll store the final video dimensions here.
        self.width = None
        self.height = None

    def start(self):
        # Open an output container for writing MP4 using PyAV.
        self.container = av.open(self.filename, mode='w')

        # Determine video dimensions based on crop region or full screen.
        if self.crop_region:
            width = self.crop_region['width']
            height = self.crop_region['height']
        else:
            with mss.mss() as sct:
                monitor = sct.monitors[1]
                width = monitor['width']
                height = monitor['height']

        # Ensure width and height are even (libx264 requirement).
        if width % 2 != 0:
            width -= 1
        if height % 2 != 0:
            height -= 1

        self.width = width
        self.height = height

        # Set up video stream using H.264 encoder.
        self.video_stream = self.container.add_stream('libx264', rate=self.fps)
        self.video_stream.width = self.width
        self.video_stream.height = self.height
        self.video_stream.pix_fmt = 'yuv420p'

        # Set up audio stream if enabled.
        if self.enable_audio:
            self.audio_stream_pyav = self.container.add_stream('aac', rate=self.audio_rate, layout='stereo')
            # Open PyAudio stream for audio capture.
            self.audio_stream = self.audio_interface.open(format=pyaudio.paInt16,
                                                          channels=self.audio_channels,
                                                          rate=self.audio_rate,
                                                          input=True,
                                                          frames_per_buffer=self.chunk)

        self.running = True
        self.start_time = time.time()
        self.video_thread = threading.Thread(target=self.record_screen)
        self.video_thread.start()
        if self.enable_audio:
            self.audio_thread = threading.Thread(target=self.record_audio)
            self.audio_thread.start()

    def stop(self):
        self.running = False
        self.video_thread.join()
        if self.enable_audio:
            self.audio_thread.join()

        # Flush any remaining packets from the video encoder.
        for packet in self.video_stream.encode():
            with self.lock:
                self.container.mux(packet)
        # Flush any remaining packets from the audio encoder if audio was enabled.
        if self.enable_audio:
            for packet in self.audio_stream_pyav.encode(None):
                with self.lock:
                    self.container.mux(packet)

        self.container.close()
        if self.enable_audio:
            self.audio_stream.stop_stream()
            self.audio_stream.close()
            self.audio_interface.terminate()

    def record_screen(self):
        with mss.mss() as sct:
            if self.crop_region:
                monitor = {
                    "top": self.crop_region['y'],
                    "left": self.crop_region['x'],
                    "width": self.crop_region['width'],
                    "height": self.crop_region['height']
                }
            else:
                monitor = sct.monitors[1]
            while self.running:
                img = sct.grab(monitor)
                frame = np.array(img)
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                current_h, current_w, _ = frame.shape
                if current_w != self.width or current_h != self.height:
                    frame = cv2.resize(frame, (self.width, self.height))
                video_frame = av.VideoFrame.from_ndarray(frame, format='bgr24')
                pts = time.time() - self.start_time
                video_frame.pts = int(pts * self.fps)
                video_frame.time_base = Fraction(1, self.fps)
                packets = self.video_stream.encode(video_frame)
                with self.lock:
                    for packet in packets:
                        self.container.mux(packet)
                time.sleep(1 / self.fps)

    def record_audio(self):
        audio_frame_count = 0
        while self.running:
            try:
                data = self.audio_stream.read(self.chunk, exception_on_overflow=False)
            except Exception:
                continue
            audio_np = np.frombuffer(data, np.int16)
            if len(audio_np) % self.audio_channels != 0:
                continue
            audio_np = audio_np.reshape(-1, self.audio_channels)
            frame = av.AudioFrame.from_ndarray(audio_np, format='s16', layout='stereo')
            frame.sample_rate = self.audio_rate
            pts = audio_frame_count * self.chunk / self.audio_rate
            frame.pts = int(pts * self.audio_rate)
            frame.time_base = Fraction(1, self.audio_rate)
            packets = self.audio_stream_pyav.encode(frame)
            with self.lock:
                for packet in packets:
                    self.container.mux(packet)
            audio_frame_count += 1

# ============================
# GUI with customtkinter
# ============================
class RecorderApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Screen Recorder")
        self.geometry("400x400")
        self.crop_region = None

        # Label for displaying selected crop region.
        self.label_crop = ctk.CTkLabel(self, text="Selected Crop Region: Full Screen")
        self.label_crop.pack(pady=10)

        # Button to select crop region via mouse.
        self.select_crop_button = ctk.CTkButton(self, text="Select Crop Region", command=self.select_crop_region)
        self.select_crop_button.pack(pady=5)

        # Entry for output filename.
        self.entry_filename = ctk.CTkEntry(self, placeholder_text="Output Filename (e.g., output.mp4)")
        self.entry_filename.pack(pady=10)
        self.entry_filename.insert(0, "output.mp4")

        # Check box for enabling audio recording.
        self.audio_checkbox = ctk.CTkCheckBox(self, text="Record Audio")
        self.audio_checkbox.select()  # Default is enabled.
        self.audio_checkbox.pack(pady=5)

        # Start and Stop buttons.
        self.start_button = ctk.CTkButton(self, text="Start Recording", command=self.start_recording)
        self.start_button.pack(pady=10)
        self.stop_button = ctk.CTkButton(self, text="Stop Recording", command=self.stop_recording, state="disabled")
        self.stop_button.pack(pady=10)

        self.recorder = None

    def select_crop_region(self):
        self.crop_win = tk.Toplevel(self)
        self.crop_win.attributes('-fullscreen', True)
        self.crop_win.attributes('-alpha', 0.3)
        self.crop_win.config(bg='black')
        self.crop_canvas = tk.Canvas(self.crop_win, cursor="cross", bg="gray")
        self.crop_canvas.pack(fill=tk.BOTH, expand=True)
        self.crop_canvas.bind("<ButtonPress-1>", self.on_crop_button_press)
        self.crop_canvas.bind("<B1-Motion>", self.on_crop_mouse_drag)
        self.crop_canvas.bind("<ButtonRelease-1>", self.on_crop_button_release)

    def on_crop_button_press(self, event):
        self.start_x = event.x
        self.start_y = event.y
        self.rect_id = self.crop_canvas.create_rectangle(self.start_x, self.start_y, self.start_x, self.start_y, outline="red", width=2)

    def on_crop_mouse_drag(self, event):
        self.crop_canvas.coords(self.rect_id, self.start_x, self.start_y, event.x, event.y)

    def on_crop_button_release(self, event):
        end_x = event.x
        end_y = event.y
        x = min(self.start_x, end_x)
        y = min(self.start_y, end_y)
        width = abs(end_x - self.start_x)
        height = abs(end_y - self.start_y)
        self.crop_region = {"x": x, "y": y, "width": width, "height": height}
        self.label_crop.configure(text=f"Selected Crop Region: x={x}, y={y}, width={width}, height={height}")
        self.crop_win.destroy()

    def start_recording(self):
        enable_audio = self.audio_checkbox.get()
        crop_region = self.crop_region if self.crop_region is not None else None
        filename = self.entry_filename.get() if self.entry_filename.get() else "output.mp4"
        downloads_folder = os.path.join(os.path.expanduser("~"), "Downloads")
        filename = os.path.join(downloads_folder, filename)
        self.recorder = ScreenRecorder(crop_region=crop_region, filename=filename, enable_audio=enable_audio)
        self.recorder.start()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        messagebox.showinfo("Recording", "Recording started!")
        print("Recording started...")

    def stop_recording(self):
        if self.recorder:
            self.recorder.stop()
            self.recorder = None
            self.start_button.configure(state="normal")
            self.stop_button.configure(state="disabled")
            messagebox.showinfo("Recording", "Recording stopped and saved.")
            print("Recording stopped and saved.")

# ============================
# Run the App
# ============================
if __name__ == "__main__":
    app = RecorderApp()
    app.mainloop()
