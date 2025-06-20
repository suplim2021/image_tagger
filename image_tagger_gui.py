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
from tkinter import filedialog
from ttkbootstrap import ttk
import ttkbootstrap as tb
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except Exception:  # pragma: no cover - optional dependency may not exist
    DND_FILES = None
    TkinterDnD = None
import threading
import pyexiv2
import time
import tkinter.messagebox as messagebox
from collections import deque
import warnings
import textwrap
from ttkbootstrap.icons import Emoji

VERSION = "1.2.4"

# Store basic GUI settings between sessions
SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")


def log_error(message, log_file="error_log.txt"):
    """Print error message and append it to a log file."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {message}\n"
    print(log_entry.strip())
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(log_entry)
    except Exception as e:
        print(f"Failed to write to log file: {e}")


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


def parse_json_content(content):
    """Parse JSON that may be wrapped in Markdown code fences and handle minor corruption."""
    if not content:
        return None

    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.startswith("json"):
            content = content[4:].lstrip()
        if content.endswith("```"):
            content = content[:-3].strip()

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    import re
    match = re.search(r"\{.*\}|\[.*\]", content, re.DOTALL)
    if match:
        snippet = match.group(0)
    else:
        snippet = content

    # Attempt to parse by progressively trimming trailing characters
    for end in range(len(snippet), 0, -1):
        sub = snippet[:end].strip()
        # remove trailing commas before closing brackets
        sub = re.sub(r",\s*([\]\}])", r"\1", sub)
        try:
            return json.loads(sub)
        except json.JSONDecodeError:
            continue
    return None


def get_thumbnail(image_path, max_size=(800, 800)):
    try:
        with Image.open(image_path) as img:
            if img.mode in ('RGBA', 'LA'):
                background = Image.new('RGB', img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[3] if img.mode == 'RGBA' else img.split()[1])
                img = background
            img.thumbnail(max_size)
            buffered = io.BytesIO()
            img.save(buffered, format="JPEG", quality=85)
            return base64.b64encode(buffered.getvalue()).decode('utf-8')
    except Exception as e:
        print(f"Error creating thumbnail for {image_path}: {str(e)}")
        return None


def process_images_batch(image_paths, model, authors):
    """Process a batch of images in a single Claude request."""
    messages_content = []
    valid_paths = []
    results = {}

    for path in image_paths:
        base64_thumbnail = get_thumbnail(path)
        if base64_thumbnail is None:
            results[path] = {"title": "Error Processing Image", "tags": ["error"], "authors": authors}
        else:
            messages_content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": base64_thumbnail,
                    },
                }
            )
            valid_paths.append(path)

    if not messages_content:
        return results

    try:
        response = client.messages.create(
            model=model,
            max_tokens=4000,
            temperature=0,
            system=(
                "You are a popular AdobeStock contributor. "
                "For each provided image, generate a title and exactly 49 relevant tags "
                "optimized for Adobe Stock. Use simple, clear, and searchable words. "
                "Sort tags by relevance, focusing on the subject's appearance, clothing, "
                "action, setting, and mood. Avoid repetition and ensure the tags cover "
                "key aspects like gender, age, ethnicity (if clear), posture, "
                "accessories, and environment. Format the response as a JSON array "
                "where each element corresponds to the input image order and contains "
                "'title' and 'tags' keys."
            ),
            messages=[{"role": "user", "content": messages_content}],
        )

        content = response.content[0].text if response.content else None
        if not content:
            for p in valid_paths:
                results[p] = {"title": "Unprocessed Image", "tags": ["unprocessed"], "authors": authors}
            return results

        image_data_list = parse_json_content(content)
        if image_data_list is None:
            log_error(f"Invalid JSON response for batch {valid_paths}: {content}")
            image_data_list = [{} for _ in valid_paths]

        if not isinstance(image_data_list, list):
            image_data_list = [image_data_list]

        for p, data in zip(valid_paths, image_data_list):
            if not isinstance(data, dict) or "title" not in data or "tags" not in data:
                data = {"title": "Unprocessed Image", "tags": ["unprocessed"]}
            data["authors"] = authors
            write_metadata(p, data["title"], data["tags"], data["authors"])
            results[p] = data

        # For any valid_paths without returned data (API returned fewer items)
        if len(image_data_list) < len(valid_paths):
            for p in valid_paths[len(image_data_list):]:
                results[p] = {"title": "Unprocessed Image", "tags": ["unprocessed"], "authors": authors}

        return results
    except Exception as e:
        log_error(f"Error processing batch {image_paths}: {str(e)}")
        for p in valid_paths:
            results[p] = {"title": "Error Processing Image", "tags": ["error"], "authors": authors}
        return results


def clear_metadata(file_path):
    """Remove all existing metadata from the image."""
    try:
        with pyexiv2.Image(file_path) as img:
            img.clear_exif()
            img.clear_iptc()
            img.clear_xmp()
            img.clear_comment()
            img.clear_icc()
        print(f"Cleared metadata for {file_path}")
    except Exception as e:
        print(f"Error clearing metadata from {file_path}: {str(e)}")


def write_metadata(file_path, title, keywords, authors, clear_existing=False):
    """Embed metadata directly into the given image file."""
    try:
        new_file_path = file_path

        if clear_existing:
            clear_metadata(new_file_path)

        if new_file_path.lower().endswith('.png'):
            with Image.open(new_file_path) as im:
                meta = PngImagePlugin.PngInfo()
                meta.add_text("Title", title)
                meta.add_text("Author", authors)
                meta.add_text("Keywords", ", ".join(keywords))
                meta.add_text("Description", title)
                im.save(new_file_path, "PNG", pnginfo=meta)
            with pyexiv2.Image(new_file_path) as img:
                img.modify_xmp({
                    'Xmp.dc.title': title,
                    'Xmp.dc.description': title,
                    'Xmp.dc.creator': authors,
                    'Xmp.dc.subject': keywords
                })
        else:
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
                exif_dict['Exif'][piexif.ExifIFD.UserComment] = (
                    piexif.helper.UserComment.dump(
                        json.dumps(iptc_data),
                        encoding="unicode",
                    )
                )
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
        master.title(f"Adobe Stock AI Keywording (Anthropic API) v{VERSION}")

        self.config_file = SETTINGS_FILE
        config = {}
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    config = json.load(f)
            except Exception as e:  # pragma: no cover - read failures aren't fatal
                log_error(f"Error loading settings: {e}")

        self.folder_path = tk.StringVar()
        self.is_processing = False
        self.is_paused = False
        self.total_images = 0
        self.processed_images = 0
        self.ok_count = 0
        self.error_count = 0

        self.models = [
            "claude-sonnet-4-20250514",
            "claude-3-7-sonnet-latest",
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-latest",
            "claude-3-5-sonnet-latest"
        ]
        default_model = config.get("selected_model", self.models[0])
        self.selected_model = tk.StringVar(value=default_model)

        self.max_workers = tk.IntVar(value=config.get("max_workers", 1))
        self.images_per_request = tk.IntVar(value=config.get("images_per_request", 1))
        self.authors = tk.StringVar(value=config.get("authors", ""))

        self.start_time = None
        self.request_times = deque(maxlen=50)
        self.pause_event = threading.Event()
        self.pause_event.set()

        self.image_list = {}
        self.current_index = 1

        # Emoji icons for control buttons
        self.play_icon = Emoji.get("black right-pointing triangle") or "\u25B6"
        self.pause_icon = Emoji.get("double vertical bar") or "\u23F8"
        self.stop_icon = Emoji.get("black square for stop") or "\u23F9"
        self.trash_icon = Emoji.get("wastebasket") or "\U0001F5D1"

        # Save settings when the window closes
        self.master.protocol("WM_DELETE_WINDOW", self.on_close)

        if DND_FILES is not None:
            try:
                master.drop_target_register(DND_FILES)
                master.dnd_bind("<<Drop>>", self.on_drop)
            except Exception:
                pass

        self.create_widgets()
        self.reset_state()

        self.thumbnail_size = (60, 60)  # Increased thumbnail size
        self.thumbnail_cache = {}
        self.preview_image = None
        self.sort_reverse = {
            "filename": False,
            "title": False,
            "tags": False,
            "authors": False,
        }

    def create_widgets(self):
        style = ttk.Style()
        style.configure("Treeview", rowheight=55)  # Set a fixed row height

        main_frame = ttk.Frame(self.master, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.master.columnconfigure(0, weight=1)
        self.master.rowconfigure(0, weight=1)

        # Path selection
        path_frame = ttk.Frame(main_frame)
        path_frame.grid(row=0, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=5)
        ttk.Label(path_frame, text="Path:").grid(row=0, column=0, sticky=tk.E, padx=5)
        ttk.Entry(
            path_frame,
            textvariable=self.folder_path,
            width=50,
        ).grid(row=0, column=1, sticky=(tk.W, tk.E), padx=5)
        ttk.Button(path_frame, text="Choose path", command=self.choose_folder).grid(row=0, column=2, padx=5)
        path_frame.columnconfigure(1, weight=1)

        # Settings frame
        settings_frame = ttk.LabelFrame(main_frame, text="Settings", padding="5")
        settings_frame.grid(row=1, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=10)

        ttk.Label(settings_frame, text="Model:").grid(row=0, column=0, sticky=tk.E, padx=5, pady=2)
        ttk.Combobox(
            settings_frame,
            textvariable=self.selected_model,
            values=self.models,
            state="readonly",
            width=25,
        ).grid(row=0, column=1, sticky=tk.W, padx=5, pady=2)

        ttk.Label(settings_frame, text="Authors:").grid(row=1, column=0, sticky=tk.E, padx=5, pady=2)
        ttk.Entry(
            settings_frame,
            textvariable=self.authors,
            width=30,
        ).grid(row=1, column=1, sticky=tk.W, padx=5, pady=2)

        ttk.Label(settings_frame, text="Max Workers:").grid(row=0, column=2, sticky=tk.E, padx=5, pady=2)
        ttk.Entry(
            settings_frame,
            textvariable=self.max_workers,
            width=5,
        ).grid(row=0, column=3, sticky=tk.W, padx=5, pady=2)

        ttk.Label(settings_frame, text="Images per request:").grid(row=1, column=2, sticky=tk.E, padx=5, pady=2)
        ttk.Entry(
            settings_frame,
            textvariable=self.images_per_request,
            width=5,
        ).grid(row=1, column=3, sticky=tk.W, padx=5, pady=2)

        # Control buttons with emoji icons
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=2, column=0, columnspan=3, pady=10)
        self.start_button = ttk.Button(
            button_frame,
            text=f"{self.play_icon} Start",
            command=self.start_processing,
        )
        self.start_button.pack(side=tk.LEFT, padx=5)
        self.pause_button = ttk.Button(
            button_frame,
            text=f"{self.pause_icon} Pause",
            command=self.toggle_pause,
            state=tk.DISABLED,
        )
        self.pause_button.pack(side=tk.LEFT, padx=5)
        self.stop_button = ttk.Button(
            button_frame,
            text=f"{self.stop_icon} Stop",
            command=self.stop_processing,
            state=tk.DISABLED,
        )
        self.stop_button.pack(side=tk.LEFT, padx=5)
        self.clear_button = ttk.Button(
            button_frame,
            text=f"{self.trash_icon} Clear Metadata",
            command=self.clear_all_metadata,
            state=tk.DISABLED,
        )
        self.clear_button.pack(side=tk.LEFT, padx=5)

        # Progress and stats
        progress_frame = ttk.Frame(main_frame)
        progress_frame.grid(row=3, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=5)
        self.progress = ttk.Progressbar(progress_frame, length=300, mode='determinate')
        self.progress.pack(side=tk.LEFT, padx=5)
        self.stats_label = ttk.Label(progress_frame, text="Success: 0 | Error: 0")
        self.stats_label.pack(side=tk.LEFT, padx=5)
        self.time_label = ttk.Label(progress_frame, text="Estimated: --:--:--")
        self.time_label.pack(side=tk.LEFT, padx=5)

        # Treeview
        tree_frame = ttk.Frame(main_frame)
        tree_frame.grid(row=4, column=0, columnspan=3, sticky=(tk.W, tk.E, tk.N, tk.S), pady=10)
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        columns = ("filename", "title", "tags", "authors")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="tree headings", height=15, style="Treeview")

        self.tree.heading("#0", text="Thumbnail")
        self.tree.column("#0", width=130, stretch=tk.NO)  # Adjusted width for thumbnails

        self.tree.heading(
            "filename", text="Filename",
            command=lambda: self.sort_tree("filename")
        )
        self.tree.column("filename", width=150, stretch=tk.YES)

        self.tree.heading(
            "title", text="Title",
            command=lambda: self.sort_tree("title")
        )
        self.tree.column("title", width=150, stretch=tk.YES)

        self.tree.heading(
            "tags", text="Tags",
            command=lambda: self.sort_tree("tags")
        )
        self.tree.column("tags", width=180, stretch=tk.YES)

        self.tree.heading(
            "authors", text="Authors",
            command=lambda: self.sort_tree("authors")
        )
        self.tree.column("authors", width=80, stretch=tk.NO)

        self.tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # Scrollbars for Treeview
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        vsb.grid(row=0, column=1, sticky=(tk.N, tk.S))
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        hsb.grid(row=1, column=0, sticky=(tk.W, tk.E))
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        # Alternating row colors
        self.tree.tag_configure('odd', background='#F0F0F0')
        self.tree.tag_configure('even', background='#FFFFFF')
        # Result status colors
        self.tree.tag_configure('Success', background='#CCFFCC')
        self.tree.tag_configure('Error', background='#FFCCCC')

        # Bind selection event to show preview
        self.tree.bind('<<TreeviewSelect>>', self.show_preview)

        # Preview area
        self.preview_label = ttk.Label(main_frame)
        self.preview_label.grid(row=5, column=0, columnspan=3, pady=5)

        # Status bar
        self.status_bar = ttk.Label(main_frame, text="", relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.grid(row=6, column=0, columnspan=3, sticky=(tk.W, tk.E))

        # Configure main_frame and tree_frame to expand
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(4, weight=1)

        self.add_tooltips()

    def get_thumbnail(self, image_path):
        if image_path in self.thumbnail_cache:
            return self.thumbnail_cache[image_path]

        try:
            with Image.open(image_path) as img:
                # Set a fixed height and calculate width to maintain aspect ratio
                fixed_height = 50
                max_width = 100  # Set a maximum width
                aspect_ratio = img.width / img.height
                new_width = min(int(fixed_height * aspect_ratio), max_width)

                img = img.resize((new_width, fixed_height), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                self.thumbnail_cache[image_path] = photo
                return photo
        except Exception as e:
            print(f"Error creating thumbnail for {image_path}: {str(e)}")
            return self.get_default_thumbnail()

    def get_default_thumbnail(self):
        if not hasattr(self, '_default_thumbnail'):
            img = Image.new('RGB', self.thumbnail_size, color='grey')
            self._default_thumbnail = ImageTk.PhotoImage(img)
        return self._default_thumbnail

    def add_tooltips(self):
        self.tooltip = None
        self.tooltip_id = None
        self.last_motion_time = 0
        self.hide_delay = 3000  # 3 seconds in milliseconds

        def show_tooltip(event):
            hide_tooltip()
            item = self.tree.identify_row(event.y)
            column = self.tree.identify_column(event.x)
            if item and column:
                values = self.tree.item(item)['values']
                column_name = self.tree.heading(column)['text']
                if column == '#0':
                    value = values[0] if values else ""
                else:
                    idx = int(column[1:]) - 1
                    value = f"{column_name}: {values[idx]}" if idx < len(values) else ""

                bbox = self.tree.bbox(item, column)
                if not bbox:
                    return
                x, y, _, _ = bbox
                x += self.tree.winfo_rootx() + 25
                y += self.tree.winfo_rooty() + 25

                self.tooltip = tk.Toplevel(self.tree)
                self.tooltip.wm_overrideredirect(True)
                self.tooltip.wm_geometry(f"+{x}+{y}")
                if column == '#0' and value:
                    image_path = os.path.join(self.folder_path.get(), value)
                    try:
                        with Image.open(image_path) as img:
                            img.thumbnail((400, 400))
                            photo = ImageTk.PhotoImage(img)
                    except Exception:
                        photo = None

                    if photo:
                        label = tk.Label(
                            self.tooltip,
                            image=photo,
                            background="#ffffe0",
                            relief=tk.SOLID,
                            borderwidth=1,
                        )
                        label.image = photo
                        label.pack(ipadx=1, ipady=1)
                else:
                    wrapped_text = "\n".join(textwrap.wrap(str(value), width=50))

                    label = tk.Label(
                        self.tooltip,
                        text=wrapped_text,
                        justify=tk.LEFT,
                        background="#ffffe0",
                        relief=tk.SOLID,
                        borderwidth=1,
                        font=("tahoma", "8", "normal"),
                        wraplength=300,
                    )
                    label.pack(ipadx=1, ipady=1)

                self.last_motion_time = time.time()
                check_hide_tooltip()

        def hide_tooltip():
            if self.tooltip:
                self.tooltip.destroy()
                self.tooltip = None
            if self.tooltip_id:
                self.master.after_cancel(self.tooltip_id)
                self.tooltip_id = None

        def check_hide_tooltip():
            current_time = time.time()
            if current_time - self.last_motion_time > self.hide_delay / 1000:
                hide_tooltip()
            else:
                self.tooltip_id = self.master.after(100, check_hide_tooltip)

        def on_motion(event):
            self.last_motion_time = time.time()
            show_tooltip(event)

        self.tree.bind('<Motion>', on_motion)
        self.tree.bind('<Leave>', lambda e: hide_tooltip())

    def choose_folder(self):
        folder_selected = filedialog.askdirectory()
        self.process_selected_folder(folder_selected)

    def process_selected_folder(self, folder_selected):
        if folder_selected:
            self.folder_path.set(folder_selected)
            self.reset_state()
            self.clear_tree()
            if hasattr(self, 'preview_label'):
                self.preview_label.config(image='', text='')
            self.preview_image = None
            self.update_output(f"Selected folder: {folder_selected}")
            threading.Thread(target=self.load_files, daemon=True).start()

    def on_drop(self, event):
        if not event.data:
            return
        path = event.data
        if path.startswith('{') and path.endswith('}'):
            path = path[1:-1]
        path = path.strip()
        if os.path.isdir(path):
            self.process_selected_folder(path)

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
        self.progress['value'] = 0
        self.update_stats()
        self.time_label.config(text="Estimated: --:--:--")
        self.start_button.config(state=tk.NORMAL, text=f"{self.play_icon} Start")
        self.pause_button.config(state=tk.DISABLED, text=f"{self.pause_icon} Pause")
        self.stop_button.config(state=tk.DISABLED, text=f"{self.stop_icon} Stop")
        self.clear_button.config(state=tk.DISABLED, text=f"{self.trash_icon} Clear Metadata")
        self.status_bar.config(text="")
        if hasattr(self, 'preview_label'):
            self.preview_label.config(image='', text='')
        self.preview_image = None

    def clear_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

    def load_files(self):
        folder_path = self.folder_path.get()
        image_files = [
            f
            for f in os.listdir(folder_path)
            if f.lower().endswith(
                (".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".gif")
            )
        ]
        self.total_images = len(image_files)
        self.progress["maximum"] = self.total_images

        for i, filename in enumerate(image_files):
            full_path = os.path.join(folder_path, filename)
            self.image_list[filename] = {
                "index": i + 1,
                "status": "Loaded",
                "title": "",
                "tags": "",
                "authors": ""
            }
            self.master.after(0, self.add_tree_item, filename, full_path)
            if i % 10 == 0:
                self.master.after(0, self.update_stats)

        self.master.after(0, self.update_output, f"Loaded {self.total_images} images")
        self.master.after(0, lambda: self.start_button.config(state=tk.NORMAL))
        self.master.after(0, lambda: self.clear_button.config(state=tk.NORMAL))

    def add_tree_item(self, filename, full_path):
        thumbnail = self.get_thumbnail(full_path)
        self.tree.insert(
            "",
            "end",
            iid=str(self.image_list[filename]["index"]),
            image=thumbnail,
            values=(filename, "", "", ""),
            tags=(
                'even' if self.image_list[filename]["index"] % 2 == 0 else 'odd'
            ),
        )

    def sort_tree(self, column):
        """Sort treeview by the given column."""
        reverse = self.sort_reverse.get(column, False)
        data = []
        for child in self.tree.get_children(''):
            value = self.tree.set(child, column)
            if isinstance(value, str):
                value = value.lower()
            data.append((value, child))

        data.sort(reverse=reverse)

        for index, (_, child) in enumerate(data):
            self.tree.move(child, '', index)
            tags = list(self.tree.item(child, 'tags'))
            tags = [t for t in tags if t not in ('odd', 'even')]
            tags.append('even' if (index + 1) % 2 == 0 else 'odd')
            self.tree.item(child, tags=tuple(tags))

        self.sort_reverse[column] = not reverse

    def validate_images_per_request(self):
        value = self.images_per_request.get()
        if value > 20:
            messagebox.showwarning("Batch size too large",
                                   "Images per request cannot exceed 20. Clamping to 20.")
            self.images_per_request.set(20)
        elif value < 1:
            messagebox.showwarning("Invalid batch size",
                                   "Images per request must be at least 1. Clamping to 1.")
            self.images_per_request.set(1)

    def start_processing(self):
        if not self.folder_path.get():
            self.update_output("Please select a folder first.")
            return

        self.validate_images_per_request()

        self.is_processing = True
        self.is_paused = False
        self.pause_event.set()
        self.start_button.config(state=tk.DISABLED)
        self.pause_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.NORMAL)
        self.clear_button.config(state=tk.DISABLED)
        self.progress['value'] = 0
        self.processed_images = 0
        self.ok_count = 0
        self.error_count = 0

        self.update_output("Starting processing...")
        threading.Thread(target=self.process_images, daemon=True).start()

    def toggle_pause(self):
        if self.is_paused:
            self.is_paused = False
            self.pause_event.set()
            self.pause_button.config(text=f"{self.pause_icon} Pause")
            self.update_output("Processing resumed.")
        else:
            self.is_paused = True
            self.pause_event.clear()
            self.pause_button.config(text=f"{self.play_icon} Resume")
            self.update_output("Processing paused.")

    def stop_processing(self):
        self.is_processing = False
        self.is_paused = False
        self.pause_event.set()
        self.start_button.config(state=tk.NORMAL)
        self.pause_button.config(state=tk.DISABLED, text=f"{self.pause_icon} Pause")
        self.stop_button.config(state=tk.DISABLED)
        self.clear_button.config(state=tk.NORMAL if self.folder_path.get() else tk.DISABLED)
        self.update_output("Processing stopped.")

    def process_images(self):
        folder_path = self.folder_path.get()
        self.start_time = time.time()

        image_paths = [os.path.join(folder_path, filename) for filename in self.image_list]
        batch_size = max(1, self.images_per_request.get())
        batches = [
            image_paths[i: i + batch_size]
            for i in range(0, len(image_paths), batch_size)
        ]

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers.get()) as executor:
            futures = {
                executor.submit(
                    self.process_images_batch_with_rate_limit,
                    batch,
                    self.selected_model.get(),
                ): tuple(batch)
                for batch in batches
            }

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

                batch_results = future.result()
                for image_path, result in batch_results.items():
                    self.master.after(0, self.update_image_item, image_path, result)
                    self.processed_images += 1
                    self.master.after(0, self.update_progress)

        self.master.after(0, self.finalize_processing)

    def process_images_batch_with_rate_limit(self, image_paths, model):
        while True:
            if not self.is_processing:
                return {
                    p: {
                        "title": "Error Processing Image",
                        "tags": ["error"],
                        "authors": self.authors.get(),
                    }
                    for p in image_paths
                }

            self.pause_event.wait()

            if not self.is_processing:
                return {
                    p: {
                        "title": "Error Processing Image",
                        "tags": ["error"],
                        "authors": self.authors.get(),
                    }
                    for p in image_paths
                }

            current_time = time.time()

            if len(self.request_times) == 50:
                time_diff = current_time - self.request_times[0]
                if time_diff < 60:
                    sleep_time = 60 - time_diff
                    self.master.after(
                        0,
                        self.update_output,
                        f"Approaching rate limit, waiting for {sleep_time:.2f} seconds...",
                    )
                    time.sleep(sleep_time)
                    continue

            try:
                results = process_images_batch(image_paths, model, self.authors.get())
                self.request_times.append(time.time())
                return results
            except Exception as e:
                if "rate_limit_error" in str(e):
                    self.master.after(
                        0,
                        self.update_output,
                        "Rate limit hit, waiting for 60 seconds...",
                    )
                    log_error("Rate limit hit while processing batch")
                    time.sleep(60)
                    self.request_times.clear()
                else:
                    log_error(f"Error processing batch {image_paths}: {str(e)}")
                    for path in image_paths:
                        self.master.after(
                            0,
                            self.update_output,
                            f"Error processing {path}: {str(e)}",
                        )
                    return {
                        path: {
                            "title": "Error Processing Image",
                            "tags": ["error"],
                            "authors": self.authors.get(),
                        }
                        for path in image_paths
                    }

    def update_image_item(self, image_path, result):
        filename = os.path.basename(image_path)
        status = "Success" if result and 'title' in result and result['title'] != "Error Processing Image" else "Error"

        if filename in self.image_list:
            item = self.image_list[filename]
            item.update({
                "status": status,
                "title": result.get('title', ''),
                "tags": ", ".join(result.get('tags', [])),
                "authors": result.get('authors', '')
            })

            values = (filename, item['title'], item['tags'], item['authors'])

            self.tree.item(str(item['index']), values=values)
            self.tree.item(str(item['index']), tags=(status,))

            if status == "Success":
                self.ok_count += 1
            else:
                self.error_count += 1

            self.update_stats()

    def update_progress(self):
        self.progress['value'] = self.processed_images
        self.update_stats()
        self.update_time_estimate()

    def finalize_processing(self):
        self.is_processing = False
        self.start_button.config(state=tk.NORMAL)
        self.pause_button.config(state=tk.DISABLED, text=f"{self.pause_icon} Pause")
        self.stop_button.config(state=tk.DISABLED)
        self.clear_button.config(state=tk.NORMAL if self.folder_path.get() else tk.DISABLED)
        self.update_output("Processing complete.")
        self.show_completion_message()

    def clear_all_metadata(self):
        if not self.folder_path.get() or not self.image_list:
            messagebox.showwarning("No Images", "Please load a folder first.")
            return
        confirm = messagebox.askyesno(
            "Confirm",
            "Are you sure you want to clear metadata from all images?",
        )
        if not confirm:
            return

        for filename in self.image_list:
            file_path = os.path.join(self.folder_path.get(), filename)
            clear_metadata(file_path)

        messagebox.showinfo("Metadata Cleared", "All metadata has been removed.")
        self.update_output("Metadata cleared from all images.")

    def update_stats(self):
        self.stats_label.config(
            text=(
                f"Progress: {self.processed_images}/{self.total_images} | "
                f"Success: {self.ok_count} | Error: {self.error_count}"
            )
        )

    def update_output(self, message):
        self.status_bar.config(text=message)

    def update_time_estimate(self):
        if self.start_time and self.processed_images > 0:
            elapsed_time = time.time() - self.start_time
            estimated_total_time = (elapsed_time / self.processed_images) * self.total_images
            estimated_time_left = max(0, estimated_total_time - elapsed_time)

            hours, rem = divmod(estimated_time_left, 3600)
            minutes, seconds = divmod(rem, 60)

            self.time_label.config(text=f"Estimated time left: {int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}")

    def show_preview(self, event=None):
        selected = self.tree.selection()
        if not selected:
            return
        item = self.tree.item(selected[0])
        if not item['values']:
            return
        filename = item['values'][0]
        image_path = os.path.join(self.folder_path.get(), filename)
        try:
            with Image.open(image_path) as img:
                # Display a larger preview image
                img.thumbnail((600, 600))
                self.preview_image = ImageTk.PhotoImage(img)
                self.preview_label.config(image=self.preview_image, text='')
        except Exception:
            self.preview_label.config(text='Failed to load image', image='')
            self.preview_image = None

    def show_completion_message(self):
        elapsed_time = time.time() - self.start_time
        hours, rem = divmod(elapsed_time, 3600)
        minutes, seconds = divmod(rem, 60)

        message = f"Task completed!\nTotal time: {int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}"
        messagebox.showinfo("Task Complete", message)

    def save_settings(self):
        data = {
            "selected_model": self.selected_model.get(),
            "max_workers": self.max_workers.get(),
            "images_per_request": self.images_per_request.get(),
            "authors": self.authors.get(),
        }
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception as e:  # pragma: no cover - save failures aren't fatal
            log_error(f"Error saving settings: {e}")

    def on_close(self):
        self.save_settings()
        self.master.destroy()


if __name__ == "__main__":
    if TkinterDnD:
        root = TkinterDnD.Tk()
        tb.Style("flatly")
    else:
        root = tb.Window(themename="flatly")
    root.geometry("1200x700")  # Increased window size
    root.resizable(True, True)
    app = ImageTaggerApp(root)
    root.mainloop()
