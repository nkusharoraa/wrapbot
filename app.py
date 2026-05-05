import os
import re
import io
import time
import json
import tkinter as tk
import sys
from tkinter import filedialog, scrolledtext, ttk, messagebox
import threading
from datetime import datetime
import base64
from email.message import EmailMessage
import requests
import pickle

import fitz  # PyMuPDF
from PIL import Image, ImageWin, ImageOps
import win32print
import win32ui
import win32gui
import win32con
import difflib

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

import amazon_api

SCOPES = [
    'https://www.googleapis.com/auth/drive.readonly',
    'https://www.googleapis.com/auth/gmail.send'
]
TARGET_EMAIL = "nkusharoraa@gmail.com"

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Poster Print Fulfillment")
        self.root.geometry("750x600")

        self.drive_service = None
        self.gmail_service = None
        self.master_folders = {}
        self.current_items = []
        self.pdf_path = None
        self.gemini_key_index = 0

        # UI Setup - Row 1: Printer selection
        top_frame = tk.Frame(root)
        top_frame.pack(pady=(10, 2), fill=tk.X, padx=10)

        tk.Label(top_frame, text="Printer:").pack(side=tk.LEFT)
        self.printer_combo = ttk.Combobox(top_frame, state="readonly", width=30)
        self.printer_combo.pack(side=tk.LEFT, padx=5)
        self.load_printers()

        # UI Setup - Row 2: Action buttons
        btn_frame = tk.Frame(root)
        btn_frame.pack(pady=(2, 5), fill=tk.X, padx=10)

        self.btn_fetch = tk.Button(btn_frame, text="Load Orders", command=self.start_fetch_orders, bg="#2C3E50", fg="white", font=("Arial", 10, "bold"))
        self.btn_fetch.pack(side=tk.LEFT, padx=(0, 5))

        self.include_shipped_var = tk.BooleanVar(value=False)
        self.chk_shipped = tk.Checkbutton(btn_frame, text="Include Shipped", variable=self.include_shipped_var, font=("Arial", 9))
        self.chk_shipped.pack(side=tk.LEFT, padx=(0, 5))

        self.btn_select = tk.Button(btn_frame, text="Load PDF (Fallback)", command=self.start_analyze, bg="#7F8C8D", fg="white", font=("Arial", 10))
        self.btn_select.pack(side=tk.LEFT, padx=5)

        self.btn_manual = tk.Button(btn_frame, text="Add Manual Print", command=self.open_manual_dialog, bg="#8E44AD", fg="white", font=("Arial", 10))
        self.btn_manual.pack(side=tk.LEFT, padx=5)

        tk.Frame(btn_frame, width=20).pack(side=tk.LEFT)  # spacer

        self.btn_a4 = tk.Button(btn_frame, text="Print A4 [0]", command=lambda: self.start_print('A4'), font=("Arial", 10, "bold"), state=tk.DISABLED)
        self.btn_a4.pack(side=tk.LEFT, padx=5)

        self.btn_a3 = tk.Button(btn_frame, text="Print A3 [0]", command=lambda: self.start_print('A3'), font=("Arial", 10, "bold"), state=tk.DISABLED)
        self.btn_a3.pack(side=tk.LEFT, padx=5)

        # UI Setup - Row 3: Date filter + order summary
        filter_frame = tk.Frame(root)
        filter_frame.pack(padx=10, pady=(0, 3), fill=tk.X)

        tk.Label(filter_frame, text="Ship by:", font=("Arial", 9)).pack(side=tk.LEFT)
        self.ship_date_var = tk.StringVar()
        self.ship_date_combo = ttk.Combobox(filter_frame, textvariable=self.ship_date_var, state="disabled", width=12, font=("Arial", 9))
        self.ship_date_combo.pack(side=tk.LEFT, padx=(3, 10))
        self.ship_date_combo.bind("<<ComboboxSelected>>", lambda e: self.select_by_date())

        self.summary_label = tk.Label(filter_frame, text="", font=("Arial", 9), fg="#555555")
        self.summary_label.pack(side=tk.LEFT)

        # UI Setup - Row 4: Selection Table
        table_frame = tk.Frame(root)
        table_frame.pack(padx=10, pady=5, fill=tk.BOTH)

        columns = ("select", "title", "size", "pack", "category", "ship_by")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=8)

        self.tree.heading("select", text="Print?")
        self.tree.heading("title", text="Item Title")
        self.tree.heading("size", text="Size")
        self.tree.heading("pack", text="Pack")
        self.tree.heading("category", text="Category")
        self.tree.heading("ship_by", text="Ship By")

        self.tree.column("select", width=60, anchor=tk.CENTER)
        self.tree.column("title", width=295)
        self.tree.column("size", width=55, anchor=tk.CENTER)
        self.tree.column("pack", width=55, anchor=tk.CENTER)
        self.tree.column("category", width=105, anchor=tk.CENTER)
        self.tree.column("ship_by", width=100, anchor=tk.CENTER)

        self.tree.tag_configure("shipped", background="#FFF3CD")
        self.tree.tag_configure("cancelled", background="#FADADD", foreground="#8B0000")
        self.tree.tag_configure("due_today", background="#D4EDDA")
        self.tree.tag_configure("overdue", background="#FFE0B2")
        
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.bind("<Button-1>", self.on_tree_click)

        # UI Setup - Row 4: Log Area
        self.log_area = scrolledtext.ScrolledText(root, height=12, wrap=tk.WORD, font=("Consolas", 9))
        self.log_area.pack(padx=10, pady=(0, 10), fill=tk.BOTH, expand=True)

        self.log("Application started. Ready to load orders.")

    def on_tree_click(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region == "cell":
            column = self.tree.identify_column(event.x)
            if column == "#1":  # 'select' column
                item_id = self.tree.identify_row(event.y)
                tags = self.tree.item(item_id, "tags")
                if "cancelled" in tags:
                    return  # cancelled orders cannot be queued for printing
                current_val = self.tree.item(item_id, "values")[0]
                new_val = " " if current_val == "✔" else "✔"

                # Update the row values
                vals = list(self.tree.item(item_id, "values"))
                vals[0] = new_val
                self.tree.item(item_id, values=vals)
                self.update_button_counts()

    def log(self, message, is_error=False):
        try:
            prefix = "ERROR: " if is_error and not message.startswith("ERROR") else ""
            self.log_area.insert(tk.END, f"{datetime.now().strftime('%H:%M:%S')} - {prefix}{message}\n")
            self.log_area.see(tk.END)
            self.root.update_idletasks()
        except RuntimeError:
            pass

        if is_error:
            threading.Thread(target=self.send_error_email, args=(message,), daemon=True).start()

    def send_error_email(self, error_msg):
        if not self.gmail_service:
            return

        try:
            message = EmailMessage()
            message.set_content(f"The Poster Print App encountered the following event:\n\n{error_msg}")
            message['To'] = TARGET_EMAIL
            message['From'] = "me"
            message['Subject'] = "Poster Print App Alert"

            encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
            create_message = {'raw': encoded_message}

            self.gmail_service.users().messages().send(userId="me", body=create_message).execute()
        except Exception as e:
            pass

    def load_printers(self):
        try:
            printers = [printer[2] for printer in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)]
            self.printer_combo['values'] = printers
            default = win32print.GetDefaultPrinter()
            if default in printers:
                self.printer_combo.set(default)
            elif printers:
                self.printer_combo.current(0)
        except Exception as e:
            self.log(f"Warning: Could not load printers: {e}", is_error=True)

    def authenticate_services(self):
        if getattr(sys, 'frozen', False):
            app_path = os.path.dirname(sys.executable)
        else:
            app_path = os.path.dirname(os.path.abspath(__file__))
            
        token_path = os.path.join(app_path, 'token.json')
        cred_path = os.path.join(app_path, 'credentials.json')
        
        creds = None
        if os.path.exists(token_path):
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(cred_path, SCOPES)
                creds = flow.run_local_server(port=0)
            with open(token_path, 'w') as token:
                token.write(creds.to_json())
        self.drive_service = build('drive', 'v3', credentials=creds)
        self.gmail_service = build('gmail', 'v1', credentials=creds)
        
    def get_master_folders(self):
        if self.master_folders: return
        folders = ['Album Posters Python', 'Album Posters Manual', 'Album Posters Manual Freelance']
        for f in folders:
            q = f"name = '{f}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
            res = self.drive_service.files().list(q=q, spaces='drive', fields='files(id, name)').execute()
            items = res.get('files', [])
            if items:
                self.master_folders[f] = items[0]['id']

    def extract_text_from_pdf(self, pdf_path):
        text = ""
        doc = fitz.open(pdf_path)
        for page in doc:
            text += page.get_text()
        return text

    def classify_music_with_gemini(self, title, fallback_is_music):
        """Use Gemini Flash-Lite to decide if a poster title is music-related (Singer, Artist, Band).
        Automatically rotates through multiple API keys if rate limits are hit.
        """
        api_keys = []
        try:
            config = amazon_api.load_config()
            # Try list first, then singular fallback
            api_keys = config.get("gemini_api_keys", [])
            if not api_keys and config.get("gemini_api_key"):
                api_keys = [config["gemini_api_key"]]
        except:
            pass

        if not api_keys:
            return fallback_is_music

        prompt = (
            f"Does this poster title contain a distinct music artist, band, or singer's name? "
            f"Reply with ONLY the word YES or NO. Title: {title}"
        )
        payload = {"contents": [{"parts": [{"text": prompt}]}]}

        max_global_retries = len(api_keys) * 2 # Allow cycling twice
        keys_exhausted_streak = 0
        
        for attempt in range(max_global_retries):
            # Rotate key index
            current_key = api_keys[self.gemini_key_index % len(api_keys)]
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={current_key}"
            
            try:
                # Add a small protective sleep only if we just hit a wall with previous keys
                if keys_exhausted_streak >= len(api_keys):
                    self.log(f"All {len(api_keys)} Gemini keys exhausted. Cooling down for 60s...")
                    time.sleep(60)
                    keys_exhausted_streak = 0
                
                resp = requests.post(url, json=payload, timeout=10)
                
                if resp.status_code == 429:
                    self.log(f"Gemini Key #{self.gemini_key_index + 1} limit hit. Rotating to next key...")
                    self.gemini_key_index = (self.gemini_key_index + 1) % len(api_keys)
                    keys_exhausted_streak += 1
                    continue

                resp.raise_for_status()
                data = resp.json()
                answer = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip().upper()
                
                # Success! We don't need a delay between different keys, but keep a tiny gap for safety
                time.sleep(1)
                
                return "YES" in answer
                
            except requests.exceptions.RequestException as e:
                # If network error, try next key just in case
                self.gemini_key_index = (self.gemini_key_index + 1) % len(api_keys)
                if attempt == max_global_retries - 1:
                    self.log(f"Gemini API completely failed after multiple retries: {e}", is_error=True)
            except Exception as e:
                break
                
        return fallback_is_music

    def parse_invoice_text(self, text):
        items = []
        
        re_desc = re.compile(r'Wrap & Wish([\s\S]*?)HSN:', re.IGNORECASE)
        for m in re_desc.finditer(text):
            full_desc = re.sub(r'\s+', ' ', m.group(1)).strip()
            
            explicit_suffix_re = re.compile(r'\sWall(?: Split)? (Posters Set|Art Frame)', re.IGNORECASE)
            explicit_match = explicit_suffix_re.search(full_desc)
            
            if explicit_match:
                title_part = full_desc[:explicit_match.start()].strip()
            else:
                wall_regex = re.compile(r'\sWall(?: Split)?(?: Posters Set| Art Frame| Posters)?', re.IGNORECASE)
                matches = list(wall_regex.finditer(full_desc))
                if matches:
                    last_wall_index = matches[-1].start()
                    title_part = full_desc[:last_wall_index].strip()
                else:
                    title_part = full_desc.strip()
                    
            title_part = re.sub(r'^Wrap & Wish\s*', '', title_part, flags=re.IGNORECASE).strip()
            title_part = re.sub(r'\s+Split\b', '', title_part, flags=re.IGNORECASE).strip()
            
            if title_part:
                items.append({
                    'searchTitle': title_part,
                    'index': m.start(),
                    'fullDesc': full_desc,
                })

        parsed_items = []
        for item in items:
            slice_text = text[item['index']:]
            size = 'OTHER'
            sku = ''
            qty = 1
            pack_val = 0
            
            size_match = re.search(r'\(A[34]\s+Size', slice_text, re.IGNORECASE)
            if size_match:
                upper = size_match.group(0).upper()
                if 'A3' in upper: size = 'A3'
                elif 'A4' in upper: size = 'A4'
                    
            sku_match = re.search(r'\(\s*([^)]+?)\s*\)[\s\r\n]*HSN:', slice_text, re.IGNORECASE)
            if sku_match:
                sku = sku_match.group(1).strip()
                
            qty_match = re.search(r'\|\s*\d+(\.\d+)?\s*\|\s*(\d+)\s*\|', slice_text)
            if not qty_match:
                qty_match = re.search(r'₹?\s*\d+(\.\d+)?\s+(\d+)\s+₹?\s*\d+(\.\d+)?', slice_text)
                
            if qty_match and qty_match.group(2):
                qty = int(qty_match.group(2))
                
            pack_match = re.search(r'\(?\s*Pack\s*of\s*(\d+)\s*\)?', item['fullDesc'], re.IGNORECASE)
            if pack_match:
                pack_val = int(pack_match.group(1))
                
            desc_lower = item['fullDesc'].lower()
            is_music_fallback = (
                'vintage music & marvel room decor' in desc_lower or
                'vintage designs & room decor' in desc_lower or
                'vintage anime designs & marvel room decor' in desc_lower or
                'anime designs & marvel room decor' in desc_lower
            )
            # Use Gemini to double check
            is_music = self.classify_music_with_gemini(item['searchTitle'], is_music_fallback)
            category = 'music' if is_music else 'new'
            
            parsed_items.append({
                'searchTitle': item['searchTitle'],
                'size': size,
                'sku': sku,
                'qty': qty,
                'pack': pack_val,
                'category': category,
            })
            
        by_sku = {}
        for it in parsed_items:
            key = (it['sku'] or it['searchTitle'], it['size'], it['pack'])
            if key not in by_sku:
                by_sku[key] = {
                    'title': it['searchTitle'],
                    'size': it['size'],
                    'totalQty': it['qty'],
                    'pack': it['pack'],
                    'category': it['category']
                }
            else:
                by_sku[key]['totalQty'] += it['qty']
                
        output = []
        for sku, data in by_sku.items():
            for _ in range(data['totalQty']):
                output.append({
                    'searchTitle': data['title'],
                    'size': data['size'],
                    'pack': data['pack'],
                    'sku': sku,
                    'category': data['category']
                })
                
        return output

    # Common words to skip when picking a Drive search keyword
    _STOP_WORDS = {'the','a','an','of','in','on','at','to','for','and','or','by','with','from','is','are','was','were'}

    def find_folder(self, folder_name):
        """Find a Drive folder whose name best matches folder_name (partial + fuzzy).
        Returns (folder_id, matched_name) or (None, None)."""
        # Pick the most distinctive word (skip stop words) to use in the Drive query
        words = folder_name.split()
        search_word = next(
            (w for w in words if w.lower() not in self._STOP_WORDS and len(w) > 2),
            words[0] if words else folder_name
        )

        query = (
            f"name contains '{search_word}' "
            f"and mimeType = 'application/vnd.google-apps.folder' "
            f"and trashed = false"
        )
        results = self.drive_service.files().list(
            q=query, spaces='drive',
            fields='nextPageToken, files(id, name)', pageSize=500
        ).execute()
        items = results.get('files', [])

        if not items:
            return None, None

        names = [item['name'] for item in items]

        # Try progressively lower cutoffs so we always get a best guess
        for cutoff in (0.6, 0.5, 0.4):
            matches = difflib.get_close_matches(folder_name, names, n=1, cutoff=cutoff)
            if matches:
                best = matches[0]
                for item in items:
                    if item['name'] == best:
                        return item['id'], item['name']

        # Fallback: pick whichever candidate has the highest ratio
        best_item = max(
            items,
            key=lambda x: difflib.SequenceMatcher(None, folder_name.lower(), x['name'].lower()).ratio()
        )
        return best_item['id'], best_item['name']
        
    def fuzzy_find_artist_folder(self, parent_id, artist_name, exact_match=False):
        q = f"'{parent_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        res = self.drive_service.files().list(q=q, spaces='drive', fields='nextPageToken, files(id, name)', pageSize=1000).execute()
        items = res.get('files', [])
        while getattr(res, 'nextPageToken', None):
            res = self.drive_service.files().list(q=q, spaces='drive', fields='nextPageToken, files(id, name)', pageSize=1000, pageToken=res['nextPageToken']).execute()
            items.extend(res.get('files', []))
            
        if exact_match:
            for item in items:
                if item['name'].lower() == artist_name.lower():
                    return item['id'], item['name']
            return None, None
            
        names = [item['name'] for item in items]
        matches = difflib.get_close_matches(artist_name, names, n=1, cutoff=0.6)
        if matches:
            best_match = matches[0]
            for item in items:
                if item['name'] == best_match:
                    return item['id'], best_match
        return None, None
        
    def find_pack_folder(self, artist_folder_id, pack_size):
        pack_str = f"pack of {pack_size}"
        
        q = f"'{artist_folder_id}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        res = self.drive_service.files().list(q=q, spaces='drive', fields='files(id, name)').execute()
        items = res.get('files', [])
        
        # 1. Level 1 check
        for item in items:
            if item['name'].lower().strip() == pack_str.lower():
                return item['id']
                
        # 2. Level 2 check (ignore YYYYYY level)
        for item in items:
            q2 = f"'{item['id']}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
            res2 = self.drive_service.files().list(q=q2, spaces='drive', fields='files(id, name)').execute()
            items2 = res2.get('files', [])
            for child in items2:
                if child['name'].lower().strip() == pack_str.lower():
                    return child['id']
                    
        return None

    def download_music_images(self, folder_id, download_dir):
        query = f"'{folder_id}' in parents and trashed = false"
        results = self.drive_service.files().list(q=query, spaces='drive', fields='nextPageToken, files(id, name, mimeType)').execute()
        items = results.get('files', [])
        
        downloaded_paths = []
        os.makedirs(download_dir, exist_ok=True)
        
        ignore_re = re.compile(r'^final[1-6]', re.IGNORECASE)
            
        for item in items:
            if item.get('mimeType', '').startswith('application/vnd.google-apps.'):
                continue
                
            name = item['name']
            if name.lower() == 'desktop.ini':
                continue
            if ignore_re.match(name):
                self.log(f"Skipping final file: {name}")
                continue

            self.log(f"Downloading Music Poster: {name}...")
            try:
                request = self.drive_service.files().get_media(fileId=item['id'])
                file_path = os.path.join(download_dir, name)
                with io.FileIO(file_path, 'wb') as fh:
                    downloader = MediaIoBaseDownload(fh, request)
                    done = False
                    while done is False:
                        status, done = downloader.next_chunk()
                downloaded_paths.append(file_path)
            except Exception as e:
                self.log(f"Error downloading {name}: {e}", is_error=True)
                
        return downloaded_paths

    def download_files_in_folder(self, folder_id, download_dir):
        query = f"'{folder_id}' in parents and trashed = false"
        results = self.drive_service.files().list(q=query, spaces='drive', fields='nextPageToken, files(id, name, mimeType)').execute()
        items = results.get('files', [])
        
        downloaded_paths = []
        os.makedirs(download_dir, exist_ok=True)
            
        for item in items:
            if item['name'].lower() == 'desktop.ini':
                continue
            if item.get('mimeType', '').startswith('application/vnd.google-apps.'):
                self.log(f"Skipping Google Workspace document: {item['name']}")
                continue

            self.log(f"Downloading {item['name']}...")
            try:
                request = self.drive_service.files().get_media(fileId=item['id'])
                file_path = os.path.join(download_dir, item['name'])
                with io.FileIO(file_path, 'wb') as fh:
                    downloader = MediaIoBaseDownload(fh, request)
                    done = False
                    while done is False:
                        status, done = downloader.next_chunk()
                downloaded_paths.append(file_path)
            except Exception as e:
                self.log(f"Error downloading {item['name']}: {e}", is_error=True)
                
        return downloaded_paths

    def get_devmode_from_preset(self, printer_name, paper_size):
        """Prepare a PyDEVMODE object pre-filled with the memorized preset settings."""
        hprinter = win32print.OpenPrinter(printer_name)
        try:
            info = win32print.GetPrinter(hprinter, 2)
            dm = info['pDevMode']
            
            config = amazon_api.load_config()
            preset = config.get(f"{paper_size.lower()}_preset_blob")
            if preset and isinstance(preset, dict):
                dm.PaperSize = preset.get('PaperSize', dm.PaperSize)
                dm.PrintQuality = preset.get('PrintQuality', dm.PrintQuality)
                dm.Color = preset.get('Color', dm.Color)
                dm.Orientation = preset.get('Orientation', dm.Orientation)
                if 'MediaType' in preset:
                    dm.MediaType = preset['MediaType']
                if preset.get('DriverData'):
                    dm.DriverData = base64.b64decode(preset['DriverData'])
            else:
                # Basic defaults if no preset blob exists
                if paper_size.upper() == 'A3':
                    dm.PaperSize = win32con.DMPAPER_A3
                else:
                    dm.PaperSize = win32con.DMPAPER_A4
            return dm
        finally:
            win32print.ClosePrinter(hprinter)

    def print_image(self, image_path, paper_size='A4', is_music=False, confirmed_devmode=None):
        try:
            printer = self.printer_combo.get()
            if not printer:
                printer = win32print.GetDefaultPrinter()

            self.log(f"Printing {os.path.basename(image_path)} on {printer} (Size: {paper_size})")

            # Create hDC using win32gui to correctly pass confirmed_devmode, then wrap for win32ui
            hdc_handle = win32gui.CreateDC("WINSPOOL", printer, confirmed_devmode)
            hDC = win32ui.CreateDCFromHandle(hdc_handle)
            
            img = Image.open(image_path)
            if img.mode != 'RGB':
                img = img.convert('RGB')
                
            printable_w = hDC.GetDeviceCaps(win32con.HORZRES)
            printable_h = hDC.GetDeviceCaps(win32con.VERTRES)
            
            if (img.width > img.height and printable_w < printable_h) or (img.width < img.height and printable_w > printable_h):
                img = img.transpose(Image.ROTATE_90)

            if is_music:
                bg_color = "#33250a"
                if paper_size.upper() == 'A4':
                    # User specifically requested 208mm width image + 2mm margin on right
                    # total A4 is 210mm. So image_w = 208/210 * printable_w
                    target_w = int(printable_w * (208.0 / 210.0))
                    # Resize image to fit the 208mm width and full height
                    img = img.resize((target_w, printable_h), Image.Resampling.LANCZOS)
                    
                    # Create background for 210mm total width with #33250a color
                    final_img = Image.new('RGB', (printable_w, printable_h), bg_color)
                    # Paste the 208mm image on the left (0,0) - margin will be at the right
                    final_img.paste(img, (0, 0))
                    img = final_img
                elif paper_size.upper() == 'A3':
                    # User requested 420mm -> 416mm on longer side (420mm side)
                    # We need to find which side is the longer one (420mm)
                    if printable_h > printable_w:
                        # Portrait: Height is (~420mm)
                        target_h = int(printable_h * (416.0 / 420.0))
                        img = img.resize((printable_w, target_h), Image.Resampling.LANCZOS)
                        final_img = Image.new('RGB', (printable_w, printable_h), bg_color)
                        # Paste at (0,0) - margin will be at the bottom
                        final_img.paste(img, (0, 0))
                        img = final_img
                    else:
                        # Landscape: Width is (~420mm)
                        target_w = int(printable_w * (416.0 / 420.0))
                        img = img.resize((target_w, printable_h), Image.Resampling.LANCZOS)
                        final_img = Image.new('RGB', (printable_w, printable_h), bg_color)
                        # Paste at (0,0) - margin will be at the right
                        final_img.paste(img, (0, 0))
                        img = final_img
                else:
                    img = img.resize((printable_w, printable_h), Image.Resampling.LANCZOS)
            else:
                img = ImageOps.fit(img, (printable_w, printable_h), Image.Resampling.LANCZOS)

            hDC.StartDoc(image_path)
            hDC.StartPage()

            dib = ImageWin.Dib(img)
            dib.draw(hDC.GetHandleOutput(), (0, 0, img.width, img.height))

            hDC.EndPage()
            hDC.EndDoc()
            hDC.DeleteDC()
            return True
        except Exception as e:
            self.log(f"Failed to print {os.path.basename(image_path)}: {e}", is_error=True)
            return False

    def disable_inputs(self):
        self.btn_fetch.config(state=tk.DISABLED, bg="SystemButtonFace")
        self.btn_select.config(state=tk.DISABLED, bg="SystemButtonFace")
        if hasattr(self, 'btn_manual'):
            self.btn_manual.config(state=tk.DISABLED, bg="SystemButtonFace")
        if hasattr(self, 'chk_shipped'):
            self.chk_shipped.config(state=tk.DISABLED)
        if hasattr(self, 'ship_date_combo'):
            self.ship_date_combo.config(state="disabled")
        self.btn_a4.config(state=tk.DISABLED, bg="SystemButtonFace")
        self.btn_a3.config(state=tk.DISABLED, bg="SystemButtonFace")

    def enable_inputs(self, a4_count, a3_count):
        self.btn_fetch.config(state=tk.NORMAL, bg="#2C3E50", fg="white")
        self.btn_select.config(state=tk.NORMAL, bg="#7F8C8D", fg="white")
        if hasattr(self, 'btn_manual'):
            self.btn_manual.config(state=tk.NORMAL, bg="#8E44AD", fg="white")
        if hasattr(self, 'chk_shipped'):
            self.chk_shipped.config(state=tk.NORMAL)
        if hasattr(self, 'ship_date_combo') and self.current_items:
            self.ship_date_combo.config(state="readonly")
        if a4_count > 0:
            self.btn_a4.config(state=tk.NORMAL, bg="#3498DB", fg="white")
        else:
            self.btn_a4.config(state=tk.DISABLED, bg="SystemButtonFace")
            
        if a3_count > 0:
            self.btn_a3.config(state=tk.NORMAL, bg="#2980B9", fg="white")
        else:
            self.btn_a3.config(state=tk.DISABLED, bg="SystemButtonFace")

    def start_fetch_orders(self):
        self.disable_inputs()
        thread = threading.Thread(target=self.run_fetch_orders, daemon=True)
        thread.start()

    def run_fetch_orders(self):
        try:
            self.log("-------- FETCHING AMAZON ORDERS --------")

            if not self.drive_service or not self.gmail_service:
                self.log("Authenticating with Google Cloud...")
                self.authenticate_services()
                self.log("Authenticated.")

            self.get_master_folders()

            include_shipped = self.include_shipped_var.get()
            self.log("Connecting to Amazon SP-API...")
            raw_items = amazon_api.fetch_all_pending_items(include_shipped=include_shipped)
            self.log(f"Fetched {len(raw_items)} order line items from Amazon.")

            if not raw_items:
                self.log("No orders found on Amazon.", is_error=True)
                self.enable_inputs(0, 0)
                return

            self.current_items = self.parse_order_items(raw_items)

            if not self.current_items:
                self.log("Orders were fetched but no poster items could be parsed from the product titles.", is_error=True)
                self.enable_inputs(0, 0)
                return

            self._display_queue_summary(source="Amazon Orders")

        except Exception as e:
            self.log(f"Critical error fetching orders: {e}", is_error=True)
            messagebox.showerror("Error", str(e))
            self.enable_inputs(0, 0)

    def parse_order_items(self, raw_items):
        """Parse Amazon order items into the same format as parse_invoice_text.
        Handles: quantity, SKU-based size fallback, title cleaning.
        """
        output = []
        for raw in raw_items:
            full_title = raw.get('title', '')
            qty = int(raw.get('qty', 1))
            sku = raw.get('sku', '')
            order_status = raw.get('order_status', 'Unshipped')
            latest_ship_date = raw.get('latest_ship_date', '')

            # Strip 'Wrap & Wish' prefix if already present in title
            clean_title = re.sub(r'^Wrap\s*&\s*Wish\s*', '', full_title, flags=re.IGNORECASE).strip()

            # Strip pipe-separated suffix: "| Pack of X | Category Name"
            # Keep only the poster name before the first pipe
            pipe_idx = clean_title.find('|')
            if pipe_idx != -1:
                clean_title = clean_title[:pipe_idx].strip()

            # Strip dimension/size annotations like (A4 Size, 9 x 12 inch), (A3 Size), (9 x 12 inch)
            clean_title = re.sub(r'\s*\([^)]*(?:inch|cm|mm|\bsize\b|A[34])[^)]*\)', '', clean_title, flags=re.IGNORECASE).strip()
            # Strip unneded suffixes like 'Wall Posters Set', 'Wall Art Frame', etc.
            clean_title = re.sub(r'\sWall(?: Split)?(?: Posters Set| Art Frame| Posters)?', '', clean_title, flags=re.IGNORECASE).strip()
            clean_title = re.sub(r'\s+Split\b', '', clean_title, flags=re.IGNORECASE).strip()
            clean_title = re.sub(r',?\s*$', '', clean_title).strip()

            # Determine size: try SKU first (most reliable for Amazon), then check original title
            size = 'OTHER'
            sku_upper = sku.upper()
            if '_A3' in sku_upper:
                size = 'A3'
            elif '_A4' in sku_upper:
                size = 'A4'
            else:
                size_match = re.search(r'\b(A3|A4)\b', full_title, re.IGNORECASE)
                if size_match:
                    size = size_match.group(1).upper()

            # Determine if music category from full original title
            title_lower = full_title.lower()
            is_music_fallback = (
                'vintage music & marvel room decor' in title_lower or
                'vintage designs & room decor' in title_lower or
                'vintage anime designs & marvel room decor' in title_lower or
                'anime designs & marvel room decor' in title_lower
            )
            # Use Gemini to double check
            is_music = self.classify_music_with_gemini(clean_title, is_music_fallback)
            category = 'music' if is_music else 'new'

            # Extract pack size from original title
            pack_val = 0
            pack_match = re.search(r'Pack\s*of\s*(\d+)', full_title, re.IGNORECASE)
            if pack_match:
                pack_val = int(pack_match.group(1))

            if not clean_title:
                self.log(f"Could not extract clean title from: {full_title[:80]}", is_error=True)
                continue

            self.log(f"Parsed: '{clean_title}' | Size: {size} | Cat: {category} | Pack: {pack_val} | Qty: {qty}")

            # Cancelled orders: show one row only (for warning purposes, not printing)
            copies = 1 if order_status == 'Canceled' else qty
            for _ in range(copies):
                output.append({
                    'searchTitle': clean_title,
                    'size': size,
                    'pack': pack_val,
                    'sku': sku,
                    'category': category,
                    'order_status': order_status,
                    'latest_ship_date': latest_ship_date,
                })

        return output

    def _display_queue_summary(self, source="Invoice"):
        """Log the queue counts and update buttons after loading."""
        for item in self.tree.get_children():
            self.tree.delete(item)

        today_str = datetime.now().strftime("%Y-%m-%d")
        cancelled_titles = []
        unshipped_dates = set()  # collect unique ship dates for the combobox

        for item in self.current_items:
            pack_display = f"×{item['pack']}" if item.get('pack') else "—"
            order_status = item.get('order_status', 'Unshipped')
            raw_date = item.get('latest_ship_date', '')
            ship_date_str = raw_date[:10] if raw_date else ""

            if order_status == 'Canceled':
                # Only show cancelled orders whose ship-by date is today or already past
                if ship_date_str and ship_date_str > today_str:
                    continue  # future ship date — not relevant yet
                ship_by_display = "CANCELLED"
                tag = ("cancelled",)
                check = " "
                cancelled_titles.append(item['searchTitle'])

            elif order_status == 'Shipped':
                ship_by_display = "SHIPPED"
                tag = ("shipped",)
                check = "✔"

            else:  # Unshipped
                if ship_date_str:
                    try:
                        sd = datetime.strptime(ship_date_str, "%Y-%m-%d")
                        ship_by_display = sd.strftime("%d %b %Y")
                    except ValueError:
                        ship_by_display = ship_date_str
                    unshipped_dates.add(ship_date_str)
                    if ship_date_str <= today_str:
                        tag = ("overdue",) if ship_date_str < today_str else ("due_today",)
                    else:
                        tag = ()
                else:
                    ship_by_display = "—"
                    tag = ()
                check = "✔"

            self.tree.insert("", tk.END, tags=tag, values=(
                check,
                item['searchTitle'],
                item['size'],
                pack_display,
                item['category'].upper(),
                ship_by_display,
            ))

        # Populate date combobox with sorted unshipped dates; default to nearest (earliest)
        sorted_dates = sorted(unshipped_dates)
        combo_values = []
        for d in sorted_dates:
            try:
                combo_values.append(datetime.strptime(d, "%Y-%m-%d").strftime("%d %b %Y"))
            except ValueError:
                combo_values.append(d)

        if hasattr(self, 'ship_date_combo'):
            self.ship_date_combo['values'] = combo_values
            if combo_values:
                self.ship_date_combo.config(state="readonly")
                self.ship_date_var.set(combo_values[0])  # nearest date
                self.select_by_date()  # auto-apply

        self.update_button_counts()

        active_items = [x for x in self.current_items if x.get('order_status') != 'Canceled']
        cancelled_count = len(cancelled_titles)
        due_count = sum(
            1 for x in self.current_items
            if x.get('order_status') not in ('Canceled', 'Shipped')
            and (x.get('latest_ship_date', '')[:10] or 'z') <= today_str
        )

        summary_parts = [f"Nearest date: {combo_values[0] if combo_values else '—'}", f"Total active: {len(active_items)}"]
        if cancelled_count:
            summary_parts.append(f"⚠ {cancelled_count} Cancelled")
        self.summary_label.config(text="  |  ".join(summary_parts), fg="#CC0000" if cancelled_count else "#555555")

        music_items = [x for x in self.current_items if x['category'] == 'music']
        self.log(f"[{source}] Total active items: {len(active_items)} | Due today/overdue: {due_count} | Music: {len(music_items)}")

        if cancelled_titles:
            self.log(f"⚠ WARNING: {len(cancelled_titles)} CANCELLED order(s) with past/today ship date — check if already printed:", is_error=True)
            for t in cancelled_titles:
                self.log(f"   CANCELLED: {t}", is_error=True)

        self.log("Ready for printing! Load paper and press Print button.")

    def select_by_date(self):
        """Check all active items due on or before the selected ship-by date; uncheck the rest."""
        raw = self.ship_date_var.get().strip()
        cutoff = None
        for fmt in ("%d %b %Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
            try:
                cutoff = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
        if not cutoff:
            messagebox.showerror("Invalid Date", f"Could not parse '{raw}'.")
            return

        cutoff_str = cutoff.strftime("%Y-%m-%d")
        changed = 0
        for child in self.tree.get_children():
            vals = list(self.tree.item(child, "values"))
            ship_by_display = vals[5]  # "DD Mon", "SHIPPED", "CANCELLED", "—"
            tags = self.tree.item(child, "tags")

            if "cancelled" in tags:
                continue  # never toggle cancelled rows

            # Determine the item's date from the matching current_items entry
            title = vals[1]
            matched = next((x for x in self.current_items if x['searchTitle'] == title and x.get('order_status') != 'Canceled'), None)
            if not matched:
                continue

            raw_date = matched.get('latest_ship_date', '')
            item_date_str = raw_date[:10] if raw_date else ""

            if item_date_str and item_date_str <= cutoff_str:
                new_check = "✔"
            elif not item_date_str:
                new_check = "✔"  # no date info — keep selected
            else:
                new_check = " "

            if vals[0] != new_check:
                vals[0] = new_check
                self.tree.item(child, values=vals)
                changed += 1

        self.update_button_counts()
        self.log(f"Selected items due on or before {cutoff.strftime('%d %b %Y')} ({changed} rows changed).")

    def open_manual_dialog(self):
        popup = tk.Toplevel(self.root)
        popup.title("Add Manual Print")
        popup.geometry("300x380")
        popup.grab_set()

        tk.Label(popup, text="Search Title (Folder Name):").pack(pady=(10, 0))
        title_var = tk.StringVar()
        tk.Entry(popup, textvariable=title_var, width=35).pack()

        tk.Label(popup, text="Size:").pack(pady=(5, 0))
        size_var = tk.StringVar(value="A4")
        ttk.Combobox(popup, textvariable=size_var, values=["A4", "A3"], state="readonly", width=10).pack()

        tk.Label(popup, text="Category:").pack(pady=(5, 0))
        cat_var = tk.StringVar(value="music")
        ttk.Combobox(popup, textvariable=cat_var, values=["music", "new"], state="readonly", width=10).pack()

        tk.Label(popup, text="Folder Group (Music Only):").pack(pady=(5, 0))
        group_var = tk.StringVar(value="Album Posters Python")
        ttk.Combobox(popup, textvariable=group_var, values=["Album Posters Python", "Album Posters Manual", "Album Posters Manual Freelance"], state="readonly", width=30).pack()

        tk.Label(popup, text="Pack Size (for music only):").pack(pady=(5, 0))
        pack_var = tk.IntVar(value=1)
        tk.Entry(popup, textvariable=pack_var, width=10).pack()

        tk.Label(popup, text="Quantity (Copies to print):").pack(pady=(5, 0))
        qty_var = tk.IntVar(value=1)
        tk.Entry(popup, textvariable=qty_var, width=10).pack()

        def submit():
            t = title_var.get().strip()
            if not t:
                messagebox.showerror("Error", "Title cannot be empty", parent=popup)
                return
            try:
                p = pack_var.get()
                q = qty_var.get()
            except:
                messagebox.showerror("Error", "Pack and Quantity must be numbers", parent=popup)
                return

            for _ in range(q):
                self.current_items.append({
                    'searchTitle': t,
                    'size': size_var.get(),
                    'pack': p,
                    'sku': 'MANUAL',
                    'category': cat_var.get(),
                    'folder_group': group_var.get()
                })
            
            self._display_queue_summary(source="Manual Addition")
            popup.destroy()

        tk.Button(popup, text="Add to Queue", command=submit, bg="#27AE60", fg="white", font=("Arial", 10, "bold")).pack(pady=15)

    def update_button_counts(self):
        """Update the [Count] on buttons based on what is currently checked in the list."""
        a4_count = 0
        a3_count = 0
        
        for child in self.tree.get_children():
            vals = self.tree.item(child, "values")
            if vals[0] == "✔":
                size = vals[2]
                if size == "A4": a4_count += 1
                elif size == "A3": a3_count += 1
        
        self.btn_a4.config(text=f"Print A4 [{a4_count}]")
        self.btn_a3.config(text=f"Print A3 [{a3_count}]")
        self.enable_inputs(a4_count, a3_count)

    def start_analyze(self):
        self.pdf_path = filedialog.askopenfilename(title="Select Invoice PDF", filetypes=[("PDF Files", "*.pdf")])
        if not self.pdf_path:
            return

        self.disable_inputs()
        thread = threading.Thread(target=self.run_analyze, daemon=True)
        thread.start()

    def run_analyze(self):
        try:
            self.log("-------- NEW INVOICE (PDF) --------")

            if not self.drive_service or not self.gmail_service:
                self.log("Authenticating with Google Cloud...")
                self.authenticate_services()
                self.log("Authenticated successfully.")

            self.get_master_folders()

            self.log(f"Reading {os.path.basename(self.pdf_path)}...")
            text = self.extract_text_from_pdf(self.pdf_path)
            self.current_items = self.parse_invoice_text(text)

            if len(self.current_items) == 0:
                self.log(f"No posters were parsed from the invoice: {os.path.basename(self.pdf_path)}. If this is unexpected, the PDF might be an image.", is_error=True)
                self.enable_inputs(0, 0)
                return

            self._display_queue_summary(source="PDF Invoice")

        except Exception as e:
            self.log(f"Critical execution error: {e}", is_error=True)
            messagebox.showerror("Error", str(e))
            self.enable_inputs(0, 0)
            
    def start_print(self, paper_size):
        printer_name = self.printer_combo.get()
        if not printer_name:
            printer_name = win32print.GetDefaultPrinter()

        hprinter = win32print.OpenPrinter(printer_name)
        try:
            self.log(f"Opening printer window for {paper_size}. Please select your preset and hit OK.")
            
            # 1. Get current settings as a template to be updated by the dialog
            info = win32print.GetPrinter(hprinter, 2)
            dm = info['pDevMode']
            
            # 2. Open the UI. This specific combination (dm, dm, 5) is proven to show the window.
            # fMode 5 = DM_IN_PROMPT (1) | DM_OUT_DEFAULT (4). Returns integer status.
            status = win32print.DocumentProperties(0, hprinter, printer_name, dm, dm, 5)
            
            if status == 1: # IDOK (User clicked OK)
                # Capture the confirmed state to update memory
                preset_data = {
                    'PaperSize': dm.PaperSize,
                    'PrintQuality': dm.PrintQuality,
                    'Color': dm.Color,
                    'Orientation': dm.Orientation,
                    'MediaType': getattr(dm, 'MediaType', 1),
                    'DriverData': base64.b64encode(dm.DriverData).decode('utf-8') if hasattr(dm, 'DriverData') else ""
                }
                config = amazon_api.load_config()
                config[f"{paper_size.lower()}_preset_blob"] = preset_data
                
                config_path = amazon_api._get_config_path()
                with open(config_path, 'w') as f:
                    json.dump(config, f, indent=4)
                
                self.log(f"Confirmed settings. Starting {paper_size} batch...")
                self.disable_inputs()
                thread = threading.Thread(target=self.run_print_job, args=(paper_size, dm), daemon=True)
                thread.start()
            else:
                self.log("Printing was cancelled.")
        except Exception as e:
            self.log(f"Could not open printer window: {e}", is_error=True)
        finally:
            win32print.ClosePrinter(hprinter)

    def run_print_job(self, paper_size, confirmed_devmode):
        try:
            self.log(f"-------- STARTING {paper_size} PRINT JOB --------")
            
            if not self.drive_service or not self.gmail_service:
                self.log("Authenticating with Google Cloud...")
                self.authenticate_services()
                
            self.get_master_folders()
            
            # Identify checked items in the tree that match the required size
            items_to_print = []
            for child in self.tree.get_children():
                vals = self.tree.item(child, "values")
                if vals[0] == "✔" and vals[2] == paper_size:
                    # Find the original item data by index or match (here we use simple match for title/size/cat)
                    title = vals[1]
                    cat = vals[3].lower()
                    
                    # Search current_items for the actual metadata needs (pack, sku)
                    orig_item = next((x for x in self.current_items if x['searchTitle'] == title and x['size'] == paper_size and x['category'] == cat), None)
                    if orig_item:
                        items_to_print.append(orig_item)
            
            if not items_to_print:
                self.log(f"No {paper_size} posters selected to print.", is_error=True)
                return

            temp_dir = os.path.join(os.environ.get('TEMP', 'temp'), 'posters_downloads')
            
            count = 1
            total = len(items_to_print)

            for item in items_to_print:
                title = item['searchTitle']
                
                if item['category'] == 'music':
                    self.log(f"Music Item [{count}/{total}] - Processing '{title}' (Pack: {item.get('pack')})...")
                    
                    is_manual = (item.get('sku') == 'MANUAL')
                    # Use combo box selection for manual items, otherwise use standard Python root
                    primary_folder = item.get('folder_group', 'Album Posters Python') if is_manual else 'Album Posters Python'
                    
                    if primary_folder not in self.master_folders:
                        self.log(f"Could not locate '{primary_folder}' in your Google Drive folders list. Ensure you are connected to the internet.", is_error=True)
                        count += 1
                        continue
                        
                    python_id = self.master_folders[primary_folder]
                    artist_id, matched_name = self.fuzzy_find_artist_folder(python_id, title)
                    
                    if not artist_id:
                        # Check fallback/manual folders for non-manual items
                        if not is_manual:
                            manual_found = False
                            for m_folder in ['Album Posters Manual', 'Album Posters Manual Freelance']:
                                if m_folder in self.master_folders:
                                    m_id, m_name = self.fuzzy_find_artist_folder(self.master_folders[m_folder], title, exact_match=False)
                                    if m_id:
                                        self.log(f"ALERT: Artist '{title}' found in '{m_folder}'. Manual processing required.", is_error=True)
                                        manual_found = True
                                        break
                            if not manual_found:
                                self.log(f"ALERT: Artist '{title}' was completely missing from all 3 music folders.", is_error=True)
                        else:
                            self.log(f"ALERT: Artist '{title}' was not found in '{primary_folder}'.", is_error=True)
                        count += 1
                        continue
                        
                    self.log(f"Fuzzy matched artist folder: '{matched_name}'")
                    pack_id = self.find_pack_folder(artist_id, item['pack'])
                    if not pack_id:
                        self.log(f"ALERT: Could not find 'pack of {item['pack']}' folder inside '{matched_name}'.", is_error=True)
                        count += 1
                        continue
                        
                    downloaded_paths = self.download_music_images(pack_id, temp_dir)
                else:
                    self.log(f"New Item [{count}/{total}] - Searching for '{title}'...")
                    folder_id, matched_folder_name = self.find_folder(title)
                    if not folder_id:
                        self.log(f"Missing Folder in Google Drive: '{title}'", is_error=True)
                        count += 1
                        continue
                    self.log(f"Matched folder: '{matched_folder_name}'")
                    downloaded_paths = self.download_files_in_folder(folder_id, temp_dir)
                
                is_music_item = (item['category'] == 'music')
                for dp in downloaded_paths:
                    self.print_image(dp, paper_size, is_music=is_music_item, confirmed_devmode=confirmed_devmode)
                    try:
                        os.remove(dp)
                    except Exception as e:
                        pass
                
                count += 1
                
            self.log(f"Finished {paper_size} Queue!")
            messagebox.showinfo("Success", f"Finished sending {paper_size} prints to queue.")

            a4_new = [x for x in self.current_items if x['size'] == 'A4']
            a3_new = [x for x in self.current_items if x['size'] == 'A3']
            self.enable_inputs(len(a4_new), len(a3_new))
            
        except Exception as e:
            self.log(f"Critical execution error during printing: {e}", is_error=True)
            messagebox.showerror("Error", str(e))
            a4_new = [x for x in self.current_items if x['size'] == 'A4']
            a3_new = [x for x in self.current_items if x['size'] == 'A3']
            self.enable_inputs(len(a4_new), len(a3_new))


if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
