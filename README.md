# LSP Multiplexer

A simple Python tool that allows multiple Language Server Protocol (LSP) servers to be multiplexed into a single LSP connection. This is useful when you want to combine the capabilities of multiple language servers - for example, using both Pyright (for type checking) and Ruff-LSP (for linting) with a single LSP client connection.

## Installation

Requires Python 3.8 or higher.

```bash
git clone https://github.com/garyo/lsp-multiplexer
cd lsp-multiplexer
```

## Usage

The multiplexer can run either over TCP or stdio:

```bash
# Run over TCP (default port 8888)
python lsp-multiplexer.py

# Run using stdio (for editors that expect LSP over stdio)
python lsp-multiplexer.py --stdio

# Run on a specific host and port
python lsp-multiplexer.py --host localhost --port 9999
```

### Command Line Arguments

- `--stdio`: Use stdio instead of TCP
- `--host`: Host to listen on (default: 127.0.0.1)
- `--port`: Port to listen on (default: 8888)
- `--log-level`: Set logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)

### Example Configuration

Edit the server configurations in `lsp-multiplexer.py`:

```python
multiplexer = LSPMultiplexer([
    ["pyright-langserver", "--stdio"],    # Local process
    "tcp://localhost:8080",               # Remote server
    ["ruff-lsp"]                          # Another local process
])
```

Each server can be specified either as:
- A list of strings for a local process (command and arguments)
- A URL string for a remote server

### Editor Configuration

#### Neovim Example

```lua
vim.lsp.start({
    name = 'multiplexed-python',
    cmd = {'nc', 'localhost', '8888'},  -- For TCP mode
    -- or
    cmd = {'python', 'path/to/lsp-multiplexer.py', '--stdio'},  -- For stdio mode
    root_dir = vim.fs.dirname(vim.fs.find({'pyproject.toml', 'setup.py'}, { upward = true })[1]),
})
```

#### Emacs Example

```elisp
(lsp-register-client
 (make-lsp-client
  :new-connection (lsp-tcp-connection '("python" "path/to/lsp-multiplexer.py"))
  :major-modes '(python-mode)
  :server-id 'multiplexed-python))
```

## Development

For debugging, use the `--log-level DEBUG` flag to see detailed logs:

```bash
python lsp-multiplexer.py --stdio --log-level DEBUG
```

## License

MIT
