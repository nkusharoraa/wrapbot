import os
import time
import win32print
import win32ui
import win32con
from PIL import Image, ImageWin
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from dotenv import load_dotenv

load_dotenv()

PRINT_QUEUE_PATH = os.getenv("PRINT_QUEUE_PATH", "print_queue")
PRINTER_NAME = os.getenv("PRINTER_NAME", "")
if not PRINTER_NAME:
    PRINTER_NAME = None

def print_image(image_path, paper_size='A4'):
    try:
        printer = PRINTER_NAME if PRINTER_NAME else win32print.GetDefaultPrinter()
        print(f"Printing {image_path} on {printer} (Size: {paper_size})")

        hprinter = win32print.OpenPrinter(printer)
        devmode = win32print.GetPrinter(hprinter, 2)["pDevMode"]
        
        if paper_size.upper() == 'A3':
            devmode.PaperSize = win32con.DMPAPER_A3
        else:
            devmode.PaperSize = win32con.DMPAPER_A4
            
        hDC = win32ui.CreateDC()
        hDC.CreatePrinterDC(printer)
        
        printable_w = hDC.GetDeviceCaps(win32con.HORZRES)
        printable_h = hDC.GetDeviceCaps(win32con.VERTRES)

        img = Image.open(image_path)
        # Convert to RGB if necessary for printing
        if img.mode != 'RGB':
            img = img.convert('RGB')
            
        # Optional: rotate image to fit the paper orientation
        # If the image is landscape but the paper is portrait (or vice versa), rotate it
        if (img.width > img.height and printable_w < printable_h) or (img.width < img.height and printable_w > printable_h):
            img = img.transpose(Image.ROTATE_90)
            
        # Scale the image down to fit the printable area
        img.thumbnail((printable_w, printable_h), Image.Resampling.LANCZOS)
        
        # Center the image
        x_offset = (printable_w - img.size[0]) // 2
        y_offset = (printable_h - img.size[1]) // 2

        hDC.StartDoc(image_path)
        hDC.StartPage()

        dib = ImageWin.Dib(img)
        dib.draw(hDC.GetHandleOutput(), (x_offset, y_offset, x_offset + img.size[0], y_offset + img.size[1]))

        hDC.EndPage()
        hDC.EndDoc()
        hDC.DeleteDC()
        win32print.ClosePrinter(hprinter)
        
        print(f"Successfully printed: {image_path}")
        return True
    except Exception as e:
        print(f"Failed to print {image_path}: {e}")
        return False

class PrintQueueHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        
        file_path = event.src_path
        # Wait slightly to ensure n8n has finished writing the file
        time.sleep(2)
        
        parent_dir = os.path.basename(os.path.dirname(file_path))
        paper_size = 'A3' if 'A3' in parent_dir.upper() else 'A4'
        
        success = print_image(file_path, paper_size)
        if success:
            completed_dir = os.path.join(PRINT_QUEUE_PATH, "completed", parent_dir)
            os.makedirs(completed_dir, exist_ok=True)
            try:
                os.rename(file_path, os.path.join(completed_dir, os.path.basename(file_path)))
                print(f"Moved {os.path.basename(file_path)} to completed folder.")
            except Exception as e:
                print(f"Could not move {file_path}. It might still be locked: {e}")

if __name__ == "__main__":
    if not os.path.exists(PRINT_QUEUE_PATH):
        os.makedirs(os.path.join(PRINT_QUEUE_PATH, "A3"), exist_ok=True)
        os.makedirs(os.path.join(PRINT_QUEUE_PATH, "A4"), exist_ok=True)
        print(f"Created print queue directories at {PRINT_QUEUE_PATH}")

    event_handler = PrintQueueHandler()
    observer = Observer()
    observer.schedule(event_handler, PRINT_QUEUE_PATH, recursive=True)
    observer.start()
    
    print(f"Starting print watcher on {os.path.abspath(PRINT_QUEUE_PATH)}...")
    print(f"Make sure n8n drops A3 images in {os.path.abspath(os.path.join(PRINT_QUEUE_PATH, 'A3'))}")
    print(f"Make sure n8n drops A4 images in {os.path.abspath(os.path.join(PRINT_QUEUE_PATH, 'A4'))}")
    print("Press Ctrl+C to stop.")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
