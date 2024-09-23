import os
import base64
import json
import io
from PIL import Image, ImageTk
from PIL import PngImagePlugin
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
import shutil

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
    try:
        with Image.open(image_path) as img:
            # Convert RGBA images to RGB
            if img.mode in ('RGBA', 'LA'):
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'RGBA':
                    background.paste(img, mask=img.split()[3])  # 3 is the alpha channel
                else:
                    background.paste(img, mask=img.split()[1])  # 1 is the alpha channel for LA mode
                img = background

            img.thumbnail(max_size)
            buffered = io.BytesIO()
            img.save(buffered, format="JPEG", quality=85)
            return base64.b64encode(buffered.getvalue()).decode('utf-8')
    except Exception as e:
        print(f"Error creating thumbnail for {image_path}: {str(e)}")
        return None

def process_image(image_path, model, authors):
    try:
        base64_thumbnail = get_thumbnail(image_path)
        if base64_thumbnail is None:
            return image_path, {"title": "Error Processing Image", "tags": ["error"], "authors": authors}
    
        response = client.messages.create(
            model=model,
            max_tokens=1000,
            temperature=0,
            system="You are a popular AdobeStock contributor. Analyze the image and provide a title and exactly 49 tags that suit Adobe Stock. Sort the tags by relevance. Use simpliest words to describe title and tags. (example; people, age, races, gender, color, action and etc.)(title examples; 50 years old Muscular Dad Male Focused sport jersy Runner in Action) Format your response as a JSON object with 'title' and 'tags' keys. The 'tags' should be an array of strings.",
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
        new_image_path = write_metadata(image_path, image_data['title'], image_data['tags'], image_data['authors'])
        return new_image_path, image_data
    except Exception as e:
        print(f"Error processing {image_path}: {str(e)}")
        return image_path, {"title": "Error Processing Image", "tags": ["error"], "authors": authors}
    
def write_metadata(file_path, title, keywords, authors):
    try:
        # Create a 'tagged' folder in the same directory as the original file
        original_dir = os.path.dirname(file_path)
        tagged_dir = os.path.join(original_dir, "tagged")
        os.makedirs(tagged_dir, exist_ok=True)

        # Generate new file path in the 'tagged' folder
        base_name = os.path.basename(file_path)
        new_file_path = os.path.join(tagged_dir, base_name)

        # Copy the original file to the new location
        shutil.copy2(file_path, new_file_path)

        # Check if the file is PNG
        if new_file_path.lower().endswith('.png'):
            # For PNG, we'll use PIL's PngImagePlugin
            im = Image.open(new_file_path)
            meta = PngImagePlugin.PngInfo()

            # Add metadata as text chunks
            meta.add_text("Title", title)
            meta.add_text("Author", authors)
            meta.add_text("Keywords", ", ".join(keywords))
            meta.add_text("Description", title)  # Using title as description as well

            # Save the image with new metadata
            im.save(new_file_path, "PNG", pnginfo=meta)

            # Additionally, use pyexiv2 for XMP metadata (more standardized)
            with pyexiv2.Image(new_file_path) as img:
                img.modify_xmp({
                    'Xmp.dc.title': title,
                    'Xmp.dc.description': title,
                    'Xmp.dc.creator': authors,
                    'Xmp.dc.subject': keywords
                })

        else:
            # For JPEG and other supported formats, use both piexif and pyexiv2
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                
                exif_dict = piexif.load(new_file_path)
                
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
                piexif.insert(exif_bytes, new_file_path)

            with pyexiv2.Image(new_file_path) as img:
                img.modify_iptc({
                    'Iptc.Application2.ObjectName': title,
                    'Iptc.Application2.Keywords': keywords,
                    'Iptc.Application2.Writer': authors
                })

        print(f"Metadata added to {new_file_path}")
        return new_file_path

    except Exception as e:
        print(f"Error attaching metadata to {file_path}: {str(e)}")
        return file_path

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

        self.thumbnail_size = (50, 50)  # Size for thumbnails
        self.thumbnail_cache = {}  # Cache to store thumbnails
    
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
        
        columns = ("filename", "title", "tags", "authors")
        self.tree = ttk.Treeview(self.master, columns=columns, show="tree headings", height=15)
        
        # Configure columns
        self.tree.heading("#0", text="Thumbnail")
        self.tree.column("#0", width=130, stretch=tk.NO)  # Reduced width for thumbnails
        
        self.tree.heading("filename", text="Filename")
        self.tree.column("filename", width=150, stretch=tk.YES)
        
        self.tree.heading("title", text="Title")
        self.tree.column("title", width=150, stretch=tk.YES)
        
        self.tree.heading("tags", text="Tags")
        self.tree.column("tags", width=180, stretch=tk.YES)
        
        self.tree.heading("authors", text="Authors")
        self.tree.column("authors", width=80, stretch=tk.NO)
        
        self.tree.grid(row=2, column=0, columnspan=3, padx=20, pady=10, sticky="nsew")
        
        # Set a custom row height
        style = ttk.Style()
        style.configure('Treeview', rowheight=70)  # Adjust row height
        
        # Add scrollbars
        vsb = ttk.Scrollbar(self.master, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(self.master, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.grid(row=2, column=3, sticky="ns")
        hsb.grid(row=3, column=0, columnspan=3, sticky="ew")
        
        # Add a default row to prevent errors when no folder is selected
        self.tree.insert("", "end", values=("", "", "No folder selected", "", "", "", ""))
        
        # Configure row and column weights
        self.master.grid_rowconfigure(2, weight=1)
        self.master.grid_columnconfigure(1, weight=1)

        # Add status bar
        self.status_bar = tk.Label(self.master, text="", bd=1, relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.grid(row=4, column=0, columnspan=4, sticky="ew")

        # Add tooltips
        self.add_tooltips()

        # Optionally, you can add this if you want to keep the output text
        # self.output_text = tk.Text(self.master, height=5, width=60)
        # self.output_text.grid(row=7, column=0, columnspan=3, padx=5, pady=5)
    
    def get_thumbnail(self, image_path):
        if image_path in self.thumbnail_cache:
            return self.thumbnail_cache[image_path]
        
        try:
            with Image.open(image_path) as img:
                # Set a fixed height and calculate width to maintain aspect ratio
                fixed_height = 50  # Reduced height (50% of previous 100)
                aspect_ratio = img.width / img.height
                new_width = int(fixed_height * aspect_ratio)
                
                img = img.resize((new_width, fixed_height), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                self.thumbnail_cache[image_path] = photo
                return photo
        except Exception as e:
            print(f"Error creating thumbnail for {image_path}: {str(e)}")
            return self.get_default_thumbnail()

    def add_tooltips(self):
        self.tooltip = None
        self.tooltip_id = None

        def show_tooltip(event):
            hide_tooltip()
            item = self.tree.identify_row(event.y)
            column = self.tree.identify_column(event.x)
            if item and column:
                column_name = self.tree.heading(column)['text']
                values = self.tree.item(item)['values']
                if column == '#0':  # Thumbnail column
                    value = f"Filename: {values[0]}"
                else:
                    column_index = int(column[1:]) - 1
                    if column_index < len(values):
                        value = f"{column_name}: {values[column_index]}"
                    else:
                        value = "N/A"
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
            self.load_files()  # Load files after selecting the folder
    
    def reset_state(self):
        self.is_processing = False
        self.is_paused = False
        self.total_images = 0
        self.processed_images = 0
        self.ok_count = 0
        self.error_count = 0
        self.start_time = None
        self.request_times.clear()
        
        # Reset GUI elements
        self.progress['value'] = 0
        self.update_stats()
        self.time_label.config(text="Estimated: --:--:--")
        self.start_button.config(state=tk.NORMAL)
        self.pause_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.DISABLED)
        self.status_bar.config(text="")
    
    def load_files(self):
        folder_path = self.folder_path.get()
        image_files = [f for f in os.listdir(folder_path) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.tiff', '.bmp', '.gif'))]
        self.total_images = len(image_files)
        
        self.clear_tree()
        self.image_list.clear()
        self.current_index = 1
        
        for filename in image_files:
            full_path = os.path.join(folder_path, filename)
            thumbnail = self.get_thumbnail(full_path)
            self.image_list[filename] = {
                "index": self.current_index, 
                "status": "Loaded", 
                "title": "", 
                "tags": "", 
                "authors": ""
            }
            self.tree.insert("", "end", iid=str(self.current_index), 
                            image=thumbnail, 
                            values=(filename, "", "", ""))
            self.current_index += 1
        
        self.update_stats()
        self.update_output(f"Loaded {self.total_images} images")

    def clear_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

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
        self.ok_count = 0
        self.error_count = 0
        
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
        self.progress['maximum'] = self.total_images
        self.update_stats()
        
        self.start_time = time.time()
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers.get()) as executor:
            futures = {executor.submit(self.process_image_with_rate_limit, os.path.join(folder_path, filename), self.selected_model.get()): filename for filename in self.image_list}
            
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
                
                new_image_path, result = future.result()
                original_filename = os.path.basename(new_image_path)
                
                if result:
                    status = "Success"
                    self.update_output(f"Processed {original_filename}: {result['title']}")
                    self.ok_count += 1
                else:
                    status = "Error"
                    self.update_output(f"Failed to process {original_filename}")
                    self.error_count += 1
                
                # Update the image_list with the new status and result
                if original_filename in self.image_list:
                    self.image_list[original_filename].update({
                        "status": status,
                        "title": result.get('title', ''),
                        "tags": ", ".join(result.get('tags', [])),
                        "authors": result.get('authors', '')
                    })
                    self.master.after(0, self._update_tree_item, original_filename)
                
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
                new_image_path, result = process_image(image_path, model, self.authors.get())
                self.request_times.append(time.time())
                return new_image_path, result
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
        full_path = os.path.join(self.folder_path.get(), filename)
        thumbnail = self.get_thumbnail(full_path)
        
        values = (filename, item['title'], item['tags'], item['authors'])
        
        if self.tree.exists(str(item['index'])):
            self.tree.item(str(item['index']), image=thumbnail, values=values)
        else:
            self.tree.insert("", "end", iid=str(item['index']), image=thumbnail, values=values)
        
        self.tree.tag_configure("Success", background="light green")
        self.tree.tag_configure("Error", background="light coral")
        self.tree.tag_configure("Load", background="white")
        self.tree.item(str(item['index']), tags=(item['status'],))
    
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
    root.geometry("800x600")  # Adjust to more compact
    root.resizable(True, True)  # Allow resizing
    app = ImageTaggerApp(root)
    root.mainloop()