# Anuris_ref

A command-line interface (CLI) tool for interacting with AI models through the Anuris framework.  
It provides a state-driven chat experience with support for configurable prompts, attachments, chat history, and proxy settings.  

## Features

- **Interactive Chat**  
  Conversational interface powered by the OpenAI API with streaming responses.

- **Configurable System Prompt**  
  Load custom prompts from file or inline to tailor assistant behavior.

- **Attachment Support**  
  Attach images, documents, or text files to enrich conversations.  
  Supported formats: `.jpg`, `.png`, `.pdf`, `.docx`, `.txt`, `.csv`, etc. (max 20MB per file).

- **Chat History**  
  Save, load, and clear chat sessions (including attachments and reasoning traces).

- **State Machine Design**  
  The application flow is managed using a finite state machine for clarity and robustness.

- **Customization**  
  Configure API key, model, base URL, proxy, and temperature via CLI or saved configuration file.

## Installation

Clone the repository:

```bash
git clone https://github.com/starfield17/Anuris_ref.git
cd Anuris_ref/V1
````

Install requirements:

```bash
bash install_requirement.sh
```

(Optional) Add to system path:

```bash
bash add_to_sys.sh
```

## Usage

Run the CLI:

```bash
python Anuris_rebuild.py --api-key <YOUR_API_KEY> --model <MODEL_NAME>
```

### Commands

* `/clear` – Clear chat history and attachments
* `/save [filename]` – Save chat history to a file
* `/load [filename]` – Load chat history from a file
* `/attach <file>` – Attach one or more files
* `/detach [index]` – Remove attachments
* `/files` – List current attachments
* `/help` – Show help and available commands

### Keyboard Shortcuts

* **Enter**: Send message
* **Ctrl+D**: Send message
* **Ctrl+V**: Paste text
* **Ctrl+Z / Ctrl+Y**: Undo/Redo
* **Up/Down**: Navigate history

## Configuration

Configuration is stored in `~/.anuris_config.toml`.
Run with `--save-config` to persist current options.