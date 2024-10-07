# Image Tagger

Image Tagger is a Python application that uses the Anthropic Claude API to automatically generate titles and tags for images. It processes images in bulk, adding metadata (EXIF, IPTC, and XMP) to each image file.

## Version 1.2.2

## Authorship Note

This code was written by an AI assistant (Claude) based on ideas and requirements provided by the repository owner. The implementation, structure, and specific coding decisions were made by the AI, while the concept and feature requests came from the human user.

## Features

- Bulk image processing (including PNG, JPG, JPEG, TIFF, BMP, and GIF support)
- Automatic title and 49 tag generation using AI
- Metadata (EXIF, IPTC, and XMP) insertion
- User-friendly GUI with thumbnail previews
- Pause and resume functionality
- Progress tracking and time estimation
- Support for multiple Claude model versions
- New "tagged" folder for processed images, preserving originals
- Improved error handling and rate limiting
- Tooltips for detailed information
- Alternating row colors in the file list for better readability

## Prerequisites

- Python 3.7+
- Anthropic API key

## Installation

1. Clone this repository:
   ```
   git clone https://github.com/suplim2021/image_tagger.git
   cd image_tagger
   ```

2. Install the required Python packages:
   ```
   pip install anthropic pillow piexif pyexiv2
   ```

3. Create a file named `api_key.txt` in the root directory and paste your Anthropic API key into it.

## Usage

1. Run the script:
   ```
   python image_tagger_gui.py
   ```

2. Use the GUI to:
   - Select the folder containing images
   - Choose the Claude model
   - Set the number of concurrent workers
   - Add author information
   - Start, pause, and stop processing

3. The application will process each image, generating a title and 49 tags, and adding this information as metadata to the image files in a new "tagged" folder.

## Configuration

- You can adjust the number of concurrent workers in the GUI. A higher number may process images faster but could hit API rate limits sooner.
- The application uses rate limiting to avoid exceeding API quotas. You can adjust these limits in the code if needed.

## Troubleshooting

- If you encounter rate limit errors, try reducing the number of concurrent workers.
- For any "Unprocessed Image" results, check if the image content might be considered sensitive by the AI model.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is open source and available under the [CC BY-NC 4.0](LICENSE).

## Disclaimer

This tool uses AI to generate image descriptions. Always review the generated content for accuracy and appropriateness before using it for any purpose.