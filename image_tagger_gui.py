import os
import base64
import json
import io
from PIL import Image
import anthropic
import concurrent.futures
import piexif
import piexif.helper
import tkinter as tk
from tkinter import filedialog, ttk
import threading
import pyexiv2
import time
import tkinter.messagebox as messagebox
from collections import deque
import warnings

def load_api_key(file_path='api_key.txt'):
    try:
        with open(file_path, 'r') as file:
            return file.read().strip()
    except FileNotFoundError:
        print(f"API key file not found: {file_path}")
        return None
    except Exception as e:
        print(f"Error reading API key: {str(e)}")
        return None

API_KEY = load_api_key()
if not API_KEY:
    raise ValueError("Failed to load API key. Please ensure 'api_key.txt' exists in the same directory as this script.")

client = anthropic.Anthropic(api_key=API_KEY)

def get_thumbnail(image_path, max_size=(800, 800)):
    with Image.open(image_path) as img:
        img.thumbnail(max_size)
        buffered = io.BytesIO()
        img.save(buffered, format="JPEG")
        return base64.b64encode(buffered.getvalue()).decode('utf-8')

def process_image(image_path, model):
    try:
        base64_thumbnail = get_thumbnail(image_path)
        
        response = client.messages.create(
            model=model,
            max_tokens=1000,
            temperature=0,
            system="You are a popular AdobeStock contributor. Analyze the image and provide a title and exactly 49 tags that suit Adobe Stock. Format your response as a JSON object with 'title' and 'tags' keys. The 'tags' should be an array of strings.",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": base64_thumbnail
                            }
                        }
                    ]
                }
            ]
        )

        content = response.content[0].text if response.content else None
        
        if not content:
            return image_path, {"title": "Unprocessed Image", "tags": ["unprocessed"]}

        try:
            image_data = json.loads(content)
        except json.JSONDecodeError:
            return image_path, {"title": "Unprocessed Image", "tags": ["unprocessed"]}

        if 'title' not in image_data or 'tags' not in image_data:
            return image_path, {"title": "Unprocessed Image", "tags": ["unprocessed"]}

        write_metadata(image_path, image_data['title'], image_data['tags'])
        return image_path, image_data
    except Exception as e:
        print(f"Error processing {image_path}: {str(e)}")
        return image_path, {"title": "Error Processing Image", "tags": ["error"]}

def write_metadata(file_path, title, keywords):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            
            exif_dict = piexif.load(file_path)
            
            exif_dict['0th'][piexif.ImageIFD.XPTitle] = title.encode('utf-16le')
            exif_dict['0th'][piexif.ImageIFD.ImageDescription] = title.encode('utf-8')
            
            keywords_str = ', '.join(keywords)
            exif_dict['0th'][piexif.ImageIFD.XPKeywords] = keywords_str.encode('utf-16le')
            
            iptc_data = {
                'title': title,
                'keywords': keywords
            }
            exif_dict['Exif'][piexif.ExifIFD.UserComment] = piexif.helper.UserComment.dump(json.dumps(iptc_data), encoding="unicode")
            
            exif_bytes = piexif.dump(exif_dict)
            piexif.insert(exif_bytes, file_path)

        with pyexiv2.Image(file_path) as img:
            img.modify_iptc({'Iptc.Application2.ObjectName': title})
            img.modify_iptc({'Iptc.Application2.Keywords': keywords})

        print(f"Metadata added to {file_path}")
    except Exception as e:
        print(f"Error attaching metadata to {file_path}: {str(e)}")

class ImageTaggerApp:
    def __init__(self, master):
        self.master = master
        master.title("Image Tagger")
        
        self.folder_path = tk.StringVar()
        self.is_processing = False
        self.is_paused = False
        self.total_images = 0
        self.processed_images = 0
        
        self.models = [
            "claude-3-haiku-20240307",
            "claude-3-sonnet-20240229",
            "claude-3-5-sonnet-20240620"
        ]
        self.selected_model = tk.StringVar()
        self.selected_model.set(self.models[0])
        
        self.max_workers = tk.IntVar(value=10)
        
        self.start_time = None
        self.request_times = deque(maxlen=50)
        self.pause_event = threading.Event()
        self.pause_event.set()
        
        self.create_widgets()
    
    def create_widgets(self):
        tk.Label(self.master, text="Path:").grid(row=0, column=0, sticky="e")
        tk.Entry(self.master, textvariable=self.folder_path, width=50).grid(row=0, column=1, padx=5, pady=5)
        tk.Button(self.master, text="Choose path", command=self.choose_folder).grid(row=0, column=2, padx=5, pady=5)
        
        tk.Label(self.master, text="Model:").grid(row=1, column=0, sticky="e")
        self.model_dropdown = ttk.Combobox(self.master, textvariable=self.selected_model, values=self.models, state="readonly")
        self.model_dropdown.grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        
        tk.Label(self.master, text="Max Workers:").grid(row=2, column=0, sticky="e")
        tk.Entry(self.master, textvariable=self.max_workers, width=5).grid(row=2, column=1, sticky="w", padx=5, pady=5)
        
        self.progress_frame = tk.Frame(self.master)
        self.progress_frame.grid(row=3, column=0, columnspan=3, padx=5, pady=5)
        
        self.progress = ttk.Progressbar(self.progress_frame, length=350, mode='determinate')
        self.progress.pack(side=tk.LEFT)
        
        self.counter_label = tk.Label(self.progress_frame, text="0/0 images")
        self.counter_label.pack(side=tk.LEFT, padx=(10, 0))
        
        self.time_label = tk.Label(self.master, text="Estimated time left: --:--:--")
        self.time_label.grid(row=4, column=0, columnspan=3, pady=5)
        
        self.start_button = tk.Button(self.master, text="Start", command=self.start_processing)
        self.start_button.grid(row=5, column=0, padx=5, pady=5)
        
        self.pause_button = tk.Button(self.master, text="Pause", command=self.toggle_pause, state=tk.DISABLED)
        self.pause_button.grid(row=5, column=1, padx=5, pady=5)
        
        self.stop_button = tk.Button(self.master, text="Stop", command=self.stop_processing, state=tk.DISABLED)
        self.stop_button.grid(row=5, column=2, padx=5, pady=5)
        
        self.output_text = tk.Text(self.master, height=10, width=60)
        self.output_text.grid(row=6, column=0, columnspan=3, padx=5, pady=5)
    
    def choose_folder(self):
        folder_selected = filedialog.askdirectory()
        self.folder_path.set(folder_selected)
    
    def start_processing(self):
        if not self.folder_path.get():
            self.update_output("Please select a folder first.")
            return
        
        self.is_processing = True
        self.is_paused = False
        self.pause_event.set()
        self.start_button.config(state=tk.DISABLED)
        self.pause_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.NORMAL)
        self.progress['value'] = 0
        self.processed_images = 0
        
        threading.Thread(target=self.process_images, daemon=True).start()

    def toggle_pause(self):
        if self.is_paused:
            self.is_paused = False
            self.pause_event.set()
            self.pause_button.config(text="Pause")
            self.update_output("Processing resumed.")
        else:
            self.is_paused = True
            self.pause_event.clear()
            self.pause_button.config(text="Resume")
            self.update_output("Processing paused.")

    def stop_processing(self):
        self.is_processing = False
        self.is_paused = False
        self.pause_event.set()
        self.start_button.config(state=tk.NORMAL)
        self.pause_button.config(state=tk.DISABLED, text="Pause")
        self.stop_button.config(state=tk.DISABLED)
        self.update_output("Processing stopped.")

    def process_images(self):
        folder_path = self.folder_path.get()
        image_files = [f for f in os.listdir(folder_path) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.tiff', '.bmp', '.gif'))]
        self.total_images = len(image_files)
        
        self.progress['maximum'] = self.total_images
        self.update_counter()
        
        self.start_time = time.time()
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers.get()) as executor:
            futures = {executor.submit(self.process_image_with_rate_limit, os.path.join(folder_path, filename), self.selected_model.get()): filename for filename in image_files}
            
            for future in concurrent.futures.as_completed(futures):
                if not self.is_processing:
                    for f in futures:
                        f.cancel()
                    break
                
                self.pause_event.wait()
                
                if not self.is_processing:
                    for f in futures:
                        f.cancel()
                    break
                
                image_path, result = future.result()
                filename = os.path.basename(image_path)
                
                if result:
                    self.update_output(f"Processed {filename}: {result['title']}")
                else:
                    self.update_output(f"Failed to process {filename}")
                
                self.processed_images += 1
                self.progress['value'] = self.processed_images
                self.update_counter()
                self.update_time_estimate()
                self.master.update_idletasks()
        
        self.is_processing = False
        self.start_button.config(state=tk.NORMAL)
        self.pause_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.DISABLED)
        self.update_output("Processing complete.")
        self.show_completion_message()

    def process_image_with_rate_limit(self, image_path, model):
        while True:
            if not self.is_processing:
                return image_path, None
            
            self.pause_event.wait()
            
            if not self.is_processing:
                return image_path, None
            
            current_time = time.time()
            
            if len(self.request_times) == 50:
                time_diff = current_time - self.request_times[0]
                if time_diff < 60:
                    sleep_time = 60 - time_diff
                    self.update_output(f"Approaching rate limit, waiting for {sleep_time:.2f} seconds...")
                    time.sleep(sleep_time)
                    continue
            
            try:
                result = process_image(image_path, model)
                self.request_times.append(time.time())
                return result
            except Exception as e:
                if "rate_limit_error" in str(e):
                    self.update_output("Rate limit hit, waiting for 60 seconds...")
                    time.sleep(60)
                    self.request_times.clear()
                else:
                    self.update_output(f"Error processing {image_path}: {str(e)}")
                    return image_path, None
    
    def update_counter(self):
        self.counter_label.config(text=f"{self.processed_images}/{self.total_images} images")
    
    def update_output(self, message):
        self.output_text.insert(tk.END, message + "\n")
        self.output_text.see(tk.END)

    def update_time_estimate(self):
        if self.start_time and self.processed_images > 0:
            elapsed_time = time.time() - self.start_time
            images_left = self.total_images - self.processed_images
            estimated_total_time = (elapsed_time / self.processed_images) * self.total_images
            estimated_time_left = max(0, estimated_total_time - elapsed_time)
            
            hours, rem = divmod(estimated_time_left, 3600)
            minutes, seconds = divmod(rem, 60)
            
            self.time_label.config(text=f"Estimated time left: {int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}")

    def show_completion_message(self):
        elapsed_time = time.time() - self.start_time
        hours, rem = divmod(elapsed_time, 3600)
        minutes, seconds = divmod(rem, 60)
        
        message = f"Task completed!\nTotal time: {int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}"
        messagebox.showinfo("Task Complete", message)

if __name__ == "__main__":
    root = tk.Tk()
    app = ImageTaggerApp(root)
    root.mainloop()