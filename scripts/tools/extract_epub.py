
import zipfile
import re
import os
import sys
from html.parser import HTMLParser

class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
    
    def handle_data(self, data):
        self.text.append(data)
    
    def get_text(self):
        return "".join(self.text)

def clean_html(html_content):
    parser = TextExtractor()
    parser.feed(html_content)
    return parser.get_text()

def extract_epub(epub_path, output_path):
    if not os.path.exists(epub_path):
        print(f"Error: File {epub_path} not found.")
        return

    content = []
    with zipfile.ZipFile(epub_path, 'r') as z:
        # Sort files to try and maintain some order - though naive
        file_list = sorted(z.namelist())
        for filename in file_list:
            if filename.endswith('.html') or filename.endswith('.xhtml') or filename.endswith('.htm'):
                print(f"Processing {filename}...")
                with z.open(filename) as f:
                    html_content = f.read().decode('utf-8', errors='ignore')
                    text = clean_html(html_content)
                    # Simple cleanup of extra whitespace
                    text = re.sub(r'\s+', ' ', text).strip()
                    if text:
                        content.append(f"--- File: {filename} ---\n{text}\n")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(content))
    print(f"Extracted text saved to {output_path}")

if __name__ == "__main__":
    epub_file = "d:/MyInvestmentSystem/大数投资.epub"
    output_file = "d:/MyInvestmentSystem/investment_philosophy.txt"
    extract_epub(epub_file, output_file)
