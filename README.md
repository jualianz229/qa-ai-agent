# QA AI Agent - Automated Test Scenario Generator

An intelligent AI-powered agent designed to automatically scan any website and generate professional, bulletproof QA Test Scenarios in CSV format along with an Executive Summary.

## 🚀 Features

- **Automated Web Scanning**: Uses `BeautifulSoup` to crawl and scan targeted URLs to extract DOM structures like Buttons, Forms, Links, Texts, and API Endpoints.
- **LLM-Powered Reasoning**: Leverages `google-genai` (Gemma 3 or Gemini Flash models) to think like a Senior QA Engineer—planning exhaustive Happy Paths, Negative Paths, Boundary Tests, and Security (SQLi/XSS) Checks.
- **Flawless CSV Formatting Strategy**: Strictly forces the AI to construct data using JSON logic behind the scenes, effectively eliminating any newline, separator, or double-quote corruption natively found in standard AI CSV parsers. 
- **Auto-Prefix IDs**: Intelligently creates test cases with Contextual IDs like `LGN-001` (Login), `CHK-001` (Checkout) etc., entirely replacing boring sequential numbers.
- **Executive Summary Generator**: Automatically evaluates the test strategy output to generate a `.md` risk assessment and QA Plan summary.
- **Batch Processing**: Supports scanning multiple lists of URLs reading from a simple `.txt` file list.
- **Instruction Profiles**: Capable of reading text profiles (e.g. `instructions/security_bug_profile.txt`) to hone testing on precisely specific features (e.g., "Prioritize Form Security and Auth").

## ⚙️ Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/jualianz229/qa-ai-agent.git
   cd qa-ai-agent
   ```

2. **Install the dependencies:**
   Make sure to use an isolated Python Virtual Environment (`venv` or `conda`).
   ```bash
   pip install -r requirements.txt
   ```

3. **Set up Environment Variables:**
   Create a `.env` file at the root of the project to store your Gemini API Key safely.
   ```ini
   GEMINI_API_KEY=your_gemini_api_key_here
   ```

## 🎯 Usage

To run the agent, simply execute the main script:
```bash
python agent.py
```

### Steps during Execution:
1. **Input URL**: 
   - Type a single target website (e.g. `saucedemo.com`).
   - Or, provide a batch list text file (e.g. `example_urls.txt`).
2. **Choose the CSV Delimiter**: Select `(,)` global standard, or `(;)` for localized Excel layouts.
3. **Optional Custom Instructions**: Instruct the AI directly via text input or load a testing profile (`/security_bug_profile.txt`) to override the target test paths.
4. **Result generation**: The tool will scan the DOM, offload logic to the AI, safely convert its JSON array into a bulletproof `utf-8-sig` CSV format, then write the CSV and a Markdown Executive Summary inside the `/Result` folder.

## 📁 Repository Structure

```
.
├── agent.py                 # Main orchestration pipeline
├── core/
│   ├── ai_engine.py         # Google Gemini integration, Prompt System, and Model Pooling (Retries/Rate Limits)
│   └── scanner.py           # Web HTTP crawler using BeautifulSoup & file I/O operations
├── instructions/            # Stores modular prompt directives/profiles
├── requirements.txt         # Minimal dependency tracking
├── ...
```

## 🛡️ License

Free to distribute and modify. Handle Target URL authorizations with care.

- Testing Codex automated review integration.
