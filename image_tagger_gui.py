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
import textwrap

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

def process_image(image_path, model, authors):
    try:
        base64_thumbnail = get_thumbnail(image_path)
        
        response = client.messages.create(
            model=model,
            max_tokens=1000,
            temperature=0,
            system="You are a popular AdobeStock contributor. Analyze the image and provide a title and exactly 49 tags that suit Adobe Stock. Sort the tags by relevance. Format your response as a JSON object with 'title' and 'tags' keys. The 'tags' should be an array of strings.",
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
            return image_path, {"title": "Unprocessed Image", "tags": ["unprocessed"], "authors": authors}

        try:
            image_data = json.loads(content)
        except json.JSONDecodeError:
            return image_path, {"title": "Unprocessed Image", "tags": ["unprocessed"], "authors": authors}

        if 'title' not in image_data or 'tags' not in image_data:
            return image_path, {"title": "Unprocessed Image", "tags": ["unprocessed"], "authors": authors}

        image_data['authors'] = authors
        write_metadata(image_path, image_data['title'], image_data['tags'], image_data['authors'])
        return image_path, image_data
    except Exception as e:
        print(f"Error processing {image_path}: {str(e)}")
        return image_path, {"title": "Error Processing Image", "tags": ["error"], "authors": authors}
    
def write_metadata(file_path, title, keywords, authors):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            
            exif_dict = piexif.load(file_path)
            
            exif_dict['0th'][piexif.ImageIFD.XPTitle] = title.encode('utf-16le')
            exif_dict['0th'][piexif.ImageIFD.ImageDescription] = title.encode('utf-8')
            exif_dict['0th'][piexif.ImageIFD.XPAuthor] = authors.encode('utf-16le')
            
            keywords_str = ', '.join(keywords)
            exif_dict['0th'][piexif.ImageIFD.XPKeywords] = keywords_str.encode('utf-16le')
            
            iptc_data = {
                'title': title,
                'keywords': keywords,
                'authors': authors
            }
            exif_dict['Exif'][piexif.ExifIFD.UserComment] = piexif.helper.UserComment.dump(json.dumps(iptc_data), encoding="unicode")
            
            exif_bytes = piexif.dump(exif_dict)
            piexif.insert(exif_bytes, file_path)

        with pyexiv2.Image(file_path) as img:
            img.modify_iptc({'Iptc.Application2.ObjectName': title})
            img.modify_iptc({'Iptc.Application2.Keywords': keywords})
            img.modify_iptc({'Iptc.Application2.Writer': authors})

        print(f"Metadata added to {file_path}")
    except Exception as e:
        print(f"Error attaching metadata to {file_path}: {str(e)}")

class ImageTaggerApp:
    def __init__(self, master):
        self.master = master
        master.title("Adobe Stock AI Keywording (Anthropic API)")
                
        self.folder_path = tk.StringVar()
        self.is_processing = False
        self.is_paused = False
        self.total_images = 0
        self.processed_images = 0
        self.ok_count = 0
        self.error_count = 0
        
        self.models = [
            "claude-3-haiku-20240307",
            "claude-3-sonnet-20240229",
            "claude-3-5-sonnet-20240620"
        ]
        self.selected_model = tk.StringVar()
        self.selected_model.set(self.models[0])
        
        self.max_workers = tk.IntVar(value=1)
        self.authors = tk.StringVar()
        
        self.start_time = None
        self.request_times = deque(maxlen=50)
        self.pause_event = threading.Event()
        self.pause_event.set()
        
        self.image_list = {}  # Change this to a dictionary
        self.current_index = 1
        
        self.create_widgets()
        self.reset_state()
    
    def create_widgets(self):
        # Add padding to the top section
        top_frame = tk.Frame(self.master, pady=5, padx=10)
        top_frame.grid(row=0, column=0, columnspan=3, sticky="ew")

        # Path and Choose path button
        tk.Label(top_frame, text="Path:").grid(row=0, column=0, sticky="e", padx=(0, 0))
        tk.Entry(top_frame, textvariable=self.folder_path, width=50).grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        tk.Button(top_frame, text="Choose path", command=self.choose_folder).grid(row=0, column=2, padx=5, pady=5)
        
        # Model, Authors, and Max Workers
        tk.Label(top_frame, text="Model:").grid(row=1, column=0, sticky="e", padx=(0, 0))
        self.model_dropdown = ttk.Combobox(top_frame, textvariable=self.selected_model, values=self.models, state="readonly", width=20)
        self.model_dropdown.grid(row=1, column=1, padx=5, pady=5, sticky="w")
        
        tk.Label(top_frame, text="Authors:").grid(row=2, column=0, sticky="e", padx=(0, 0))
        tk.Entry(top_frame, textvariable=self.authors, width=20).grid(row=2, column=1, padx=5, pady=5, sticky="ew")
        
        tk.Label(top_frame, text="Max Workers:").grid(row=1, column=2, sticky="w", padx=(0, 0))
        tk.Entry(top_frame, textvariable=self.max_workers, width=5).grid(row=1, column=2, padx=(100, 5), pady=5, sticky="w")
        
        # Start, Pause, and Stop buttons
        button_frame = tk.Frame(top_frame)
        button_frame.grid(row=2, column=1, sticky="e", columnspan=3, pady=10)
        
        self.start_button = tk.Button(button_frame, text="Start", command=self.start_processing)
        self.start_button.pack(side=tk.LEFT, padx=5)
        self.pause_button = tk.Button(button_frame, text="Pause", command=self.toggle_pause, state=tk.DISABLED)
        self.pause_button.pack(side=tk.LEFT, padx=5)
        self.stop_button = tk.Button(button_frame, text="Stop", command=self.stop_processing, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=5)
        
        # Progress bar, success/error count, and estimated time
        progress_frame = tk.Frame(self.master)
        progress_frame.grid(row=1, column=0, columnspan=3, sticky="ew", pady=0, padx=20)

        self.progress = ttk.Progressbar(progress_frame, length=300, mode='determinate')
        self.progress.pack(side=tk.LEFT, padx=5)
        self.stats_label = tk.Label(progress_frame, text="Success: 0 | Error: 0")
        self.stats_label.pack(side=tk.LEFT, padx=5)
        self.time_label = tk.Label(progress_frame, text="Estimated: --:--:--")
        self.time_label.pack(side=tk.LEFT, padx=5)
        
        columns = ("No.", "Status", "File Name", "Title", "Tags", "Authors")
        self.tree = ttk.Treeview(self.master, columns=columns, show="headings")
        for col in columns:
            self.tree.heading(col, text=col)
        
         # Add a default row to prevent errors when no folder is selected
        self.tree.insert("", "end", values=("", "", "No folder selected", "", "", ""))
        
        # Adjust column widths
        self.tree.column("No.", width=30, stretch=tk.NO)
        self.tree.column("Status", width=50, stretch=tk.NO)
        self.tree.column("File Name", width=100, stretch=tk.YES)
        self.tree.column("Title", width=100, stretch=tk.YES)
        self.tree.column("Tags", width=150, stretch=tk.YES)
        self.tree.column("Authors", width=70, stretch=tk.NO)
        
        self.tree.grid(row=2, column=0, columnspan=3, padx=20, pady=10, sticky="nsew")
        
        # Vertical scrollbar
        v_scrollbar = ttk.Scrollbar(self.master, orient="vertical", command=self.tree.yview)
        v_scrollbar.grid(row=2, column=3, sticky="ns")
        self.tree.configure(yscrollcommand=v_scrollbar.set)
                
        # Configure row and column weights
        self.master.grid_rowconfigure(2, weight=1)
        self.master.grid_columnconfigure(1, weight=1)

        # Add status bar
        self.status_bar = tk.Label(self.master, text="", bd=1, relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.grid(row=4, column=0, columnspan=4, sticky="ew")

        # Configure row and column weights
        self.master.grid_rowconfigure(2, weight=1)
        self.master.grid_columnconfigure(1, weight=1)

        # Add tooltips
        self.add_tooltips()

        # Optionally, you can add this if you want to keep the output text
        # self.output_text = tk.Text(self.master, height=5, width=60)
        # self.output_text.grid(row=7, column=0, columnspan=3, padx=5, pady=5)
    
    def add_tooltips(self):
        self.tooltip = None
        self.tooltip_id = None

        def show_tooltip(event):
            hide_tooltip()
            item = self.tree.identify_row(event.y)
            column = self.tree.identify_column(event.x)
            if item and column:
                column_index = int(column[1:]) - 1  # Convert #1, #2, etc. to 0, 1, etc.
                values = self.tree.item(item)['values']
                if values and len(values) > column_index:
                    value = values[column_index]
                    self.tooltip_id = self.master.after(500, lambda: create_tooltip(event, value))

        def create_tooltip(event, value):
            x = event.x_root + 15
            y = event.y_root + 10
            self.tooltip = tk.Toplevel(self.master)
            self.tooltip.wm_overrideredirect(True)
            self.tooltip.wm_geometry(f"+{x}+{y}")
            
            # Wrap long text
            wrapped_text = '\n'.join(textwrap.wrap(str(value), width=50))
            
            label = tk.Label(self.tooltip, text=wrapped_text, justify=tk.LEFT,
                            background="#ffffe0", relief=tk.SOLID, borderwidth=1,
                            font=("tahoma", "8", "normal"), wraplength=300)
            label.pack(ipadx=1, ipady=1)

        def hide_tooltip():
            if self.tooltip_id:
                self.master.after_cancel(self.tooltip_id)
                self.tooltip_id = None
            if self.tooltip:
                self.tooltip.destroy()
                self.tooltip = None

        def on_motion(event):
            hide_tooltip()
            show_tooltip(event)

        self.tree.bind("<Motion>", on_motion)
        self.tree.bind("<Leave>", lambda e: hide_tooltip())

    def choose_folder(self):
        folder_selected = filedialog.askdirectory()
        if folder_selected:  # Check if a folder was actually selected
            self.folder_path.set(folder_selected)
            self.reset_state()
            self.clear_tree()
            self.update_output(f"Selected folder: {folder_selected}")
    
    def reset_state(self):
        self.is_processing = False
        self.is_paused = False
        self.total_images = 0
        self.processed_images = 0
        self.ok_count = 0
        self.error_count = 0
        self.start_time = None
        self.request_times.clear()
        self.image_list.clear()
        self.current_index = 1
        
        # Reset GUI elements
        self.progress['value'] = 0
        self.update_stats()
        self.time_label.config(text="Estimated: --:--:--")
        self.start_button.config(state=tk.NORMAL)
        self.pause_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.DISABLED)
        self.status_bar.config(text="")

    def clear_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

    def start_processing(self):
        if not self.folder_path.get():
            self.update_output("Please select a folder first.")
            return
        
        self.clear_tree()  # Clear the tree before starting new processing
        self.reset_state()  # Reset the state before starting new processing
        
        self.is_processing = True
        self.is_paused = False
        self.pause_event.set()
        self.start_button.config(state=tk.DISABLED)
        self.pause_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.NORMAL)
        
        threading.Thread(target=self.process_images, daemon=True).start()
    
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
        
        # Populate the list with "Load" status
        for filename in image_files:
            self.image_list[filename] = {"index": self.current_index, "status": "Load", "title": "", "tags": "", "authors": ""}
            self.master.after(0, self._update_tree_item, filename)
            self.current_index += 1
        
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
                self.update_image_list(image_path, result)
                
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
                result = process_image(image_path, model, self.authors.get())
                self.request_times.append(time.time())
                if isinstance(result, tuple) and len(result) == 2:
                    return result
                else:
                    return image_path, result
            except Exception as e:
                if "rate_limit_error" in str(e):
                    self.update_output("Rate limit hit, waiting for 60 seconds...")
                    time.sleep(60)
                    self.request_times.clear()
                else:
                    self.update_output(f"Error processing {image_path}: {str(e)}")
                    return image_path, {"title": "Error Processing Image", "tags": ["error"], "authors": self.authors.get()}


    def update_image_list(self, image_path, result):
        self.master.after(0, self._update_image_list, image_path, result)

    def _update_image_list(self, image_path, result):
        filename = os.path.basename(image_path)
        status = "Success" if result and 'title' in result and result['title'] != "Error Processing Image" else "Error"
        title = result['title'] if result and 'title' in result else ""
        tags = ", ".join(result['tags']) if result and 'tags' in result else ""
        authors = result['authors'] if result and 'authors' in result else ""
        
        if filename in self.image_list:
            self.image_list[filename].update({
                "status": status,
                "title": title,
                "tags": tags,
                "authors": authors
            })
            self._update_tree_item(filename)
        
        if status == "Success":
            self.ok_count += 1
        else:
            self.error_count += 1
        
        self.update_stats()

    def _update_tree_item(self, filename):
        item = self.image_list[filename]
        values = (item['index'], item['status'], filename, item['title'], item['tags'], item['authors'])
        
        if self.tree.exists(str(item['index'])):
            self.tree.item(str(item['index']), values=values)
        else:
            self.tree.insert("", "end", iid=str(item['index']), values=values)
        
        self.tree.tag_configure("Success", background="light green")
        self.tree.tag_configure("Error", background="light coral")
        self.tree.tag_configure("Load", background="white")
        self.tree.item(str(item['index']), tags=(item['status'],))

    def process_images(self):
        folder_path = self.folder_path.get()
        image_files = [f for f in os.listdir(folder_path) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.tiff', '.bmp', '.gif'))]
        self.total_images = len(image_files)
        
        self.progress['maximum'] = self.total_images
        self.update_stats()
        
        self.start_time = time.time()
        
        # Populate the list with "Load" status
        for filename in image_files:
            self.image_list[filename] = {"index": self.current_index, "status": "Load", "title": "", "tags": "", "authors": ""}
            self.master.after(0, self._update_tree_item, filename)
            self.current_index += 1
        
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
                self.update_image_list(image_path, result)
                
                self.processed_images += 1
                self.progress['value'] = self.processed_images
                self.update_stats()
                self.update_time_estimate()
                self.master.update_idletasks()
        
        self.is_processing = False
        self.start_button.config(state=tk.NORMAL)
        self.pause_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.DISABLED)
        self.update_output("Processing complete.")
        self.show_completion_message()
    
    def update_stats(self):
        self.stats_label.config(text=f"Progress: {self.processed_images}/{self.total_images} | Success: {self.ok_count} | Error: {self.error_count}")

    def update_counter(self):
        self.counter_label.config(text=f"{self.processed_images}/{self.total_images} images")
    
    def update_output(self, message):
        self.master.after(0, self._update_output, message)

    def _update_output(self, message):
        self.status_bar.config(text=message)

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
    root.geometry("700x400")  # Increased size to accommodate the new layout
    root.resizable(False, False)
    app = ImageTaggerApp(root)
    root.mainloop()