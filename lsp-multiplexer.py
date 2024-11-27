import asyncio
import json
import sys
import argparse
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Union, Any
from urllib.parse import urlparse
import traceback
import logging

@dataclass
class ServerConfig:
    """Configuration for an LSP server"""
    command: Optional[List[str]] = None
    url: Optional[str] = None
    
    def __post_init__(self) -> None:
        if not self.command and not self.url:
            raise ValueError("Either command or url must be specified")
        if self.command and self.url:
            raise ValueError("Only one of command or url should be specified")

class LSPServer:
    def __init__(self, config: ServerConfig) -> None:
        self.config = config
        self.writer: Optional[asyncio.StreamWriter] = None
        self.reader: Optional[asyncio.StreamReader] = None
        self.stderr_reader: Optional[asyncio.StreamReader] = None
        self.capabilities: Dict[str, Union[Dict[str, Any], List[Any], str, bool]] = {}
        self.initialized = False
        self._process: Optional[asyncio.subprocess.Process] = None

    async def start(self) -> None:
        if self.config.command:
            self._process = await asyncio.create_subprocess_exec(
                *self.config.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            if self._process.stdout:
                self.reader = self._process.stdout
            if self._process.stderr:
                self.stderr_reader = self._process.stderr
            if self._process.stdin:
                self.writer = self._process.stdin

        else:
            assert self.config.url is not None
            url = urlparse(self.config.url)
            hostname = url.hostname
            if isinstance(hostname, bytes):
                hostname = hostname.decode('utf-8')
            try:
                self.reader, self.writer = await asyncio.open_connection(
                    hostname,
                    url.port or 80
                )
            except Exception as e:
                raise ConnectionError(f"Failed to connect to {self.config.url}: {e}")

    async def cleanup(self) -> None:
        if self.writer:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception:
                pass
        if self._process:
            try:
                self._process.terminate()
                await self._process.wait()
            except Exception:
                pass

class LSPMultiplexer:
    def __init__(self, server_configs: List[Union[List[str], str]]) -> None:
        self.logger = logging.getLogger(__name__)
        configs: List[ServerConfig] = []
        for config in server_configs:
            if isinstance(config, list):
                configs.append(ServerConfig(command=config))
            elif isinstance(config, str):
                configs.append(ServerConfig(url=config))
            else:
                raise ValueError(f"Invalid server configuration: {config}")
                
        self.servers: List[LSPServer] = [LSPServer(config) for config in configs]
        self.request_sources: Dict[int, Tuple[str, Set[int]]] = {}

    async def start_servers(self) -> None:
        start_tasks = [server.start() for server in self.servers]
        await asyncio.gather(*start_tasks)

    def parse_header(self, header: str) -> Dict[str, str]:
        return dict(line.split(": ", 1) for line in header.splitlines() if line)

    def create_header(self, content_length: int) -> str:
        return f"Content-Length: {content_length}\r\n\r\n"

    async def read_message(self, stream: asyncio.StreamReader) -> Optional[dict]:
        try:
            header = ""
            while True:
                line = await stream.readline()
                if not line:
                    return None
                line = line.decode('utf-8')
                if line == '\r\n':
                    break
                header += line

            header_dict = self.parse_header(header)
            content_length = int(header_dict['Content-Length'])

            content = await stream.read(content_length)
            if not content:
                return None
            
            return json.loads(content.decode('utf-8'))
        except Exception as e:
            self.logger.error(f"Error reading message: {e}")
            return None

    async def write_message(self, writer: asyncio.StreamWriter, message: dict) -> None:
        try:
            content = json.dumps(message).encode('utf-8')
            header = self.create_header(len(content)).encode('utf-8')
            writer.write(header + content)
            await writer.drain()
        except Exception as e:
            self.logger.error(f"Error writing message: {e}")

    def merge_capabilities(self) -> Dict[str, Union[Dict[str, Any], List[Any], str, bool]]:
        merged: Dict[str, Union[Dict[str, Any], List[Any], str, bool]] = {}

        try:
            for server in self.servers:
                for key, value in server.capabilities.items():
                    #self.logger.debug(f"merge_capabilities({server}): checking {key}: {value}")
                    mkey = merged.get(key) # separate var for type checker
                    if key not in merged:
                        merged[key] = value
                    elif isinstance(value, dict) and isinstance(mkey, dict):
                        mkey.update(value)
                    elif isinstance(value, list) and isinstance(mkey, list):
                        merged[key] = list(set(mkey + value))
        except KeyError as e:
            self.logger.error(f"Error merging capabilities: {e}\n{traceback.format_exc()}")
            sys.exit(1)

        return merged

    def find_server_for_request(self, method: Optional[str]) -> List[int]:
        # Methods that should always go to all servers
        all_servers_methods = {
            # Basic protocol methods
            'initialize',
            'initialized',
            'shutdown',
            'exit',

            # Document sync methods
            'textDocument/didOpen',
            'textDocument/didClose',
            'textDocument/didChange',
            'textDocument/didSave',

            # Workspace methods
            'workspace/didChangeConfiguration',
            'workspace/didChangeWorkspaceFolders',
            'workspace/didChangeWatchedFiles',
        }

        capability_map = {
            # Text Document
            'textDocument/hover': 'hoverProvider',
            'textDocument/signatureHelp': 'signatureHelpProvider',
            'textDocument/declaration': 'declarationProvider',
            'textDocument/definition': 'definitionProvider',
            'textDocument/typeDefinition': 'typeDefinitionProvider',
            'textDocument/implementation': 'implementationProvider',
            'textDocument/references': 'referencesProvider',
            'textDocument/documentHighlight': 'documentHighlightProvider',
            'textDocument/documentSymbol': 'documentSymbolProvider',
            'textDocument/codeAction': 'codeActionProvider',
            'textDocument/codeLens': 'codeLensProvider',
            'textDocument/formatting': 'documentFormattingProvider',
            'textDocument/rangeFormatting': 'documentRangeFormattingProvider',
            'textDocument/onTypeFormatting': 'documentOnTypeFormattingProvider',
            'textDocument/rename': 'renameProvider',
            'textDocument/documentLink': 'documentLinkProvider',
            'textDocument/color': 'colorProvider',
            'textDocument/foldingRange': 'foldingRangeProvider',
            'textDocument/selectionRange': 'selectionRangeProvider',
            'textDocument/semanticTokens': 'semanticTokensProvider',
            'textDocument/linkedEditingRange': 'linkedEditingRangeProvider',
            'textDocument/moniker': 'monikerProvider',
            'textDocument/inlayHint': 'inlayHintProvider',
            'textDocument/inlineValue': 'inlineValueProvider',
            'textDocument/diagnostic': 'diagnosticProvider',
            'textDocument/completion': 'completionProvider',
            'textDocument/publishDiagnostics': 'publishDiagnostics',

            # Workspace
            'workspace/symbol': 'workspaceSymbolProvider',
            'workspace/executeCommand': 'executeCommandProvider',
            'workspace/willCreateFiles': 'workspace.fileOperations.willCreate',
            'workspace/didCreateFiles': 'workspace.fileOperations.didCreate',
            'workspace/willRenameFiles': 'workspace.fileOperations.willRename',
            'workspace/didRenameFiles': 'workspace.fileOperations.didRename',
            'workspace/willDeleteFiles': 'workspace.fileOperations.willDelete',
            'workspace/didDeleteFiles': 'workspace.fileOperations.didDelete',
        }

        if not method or method in all_servers_methods:
            return list(range(len(self.servers)))

        supporting_servers = []
        for i, server in enumerate(self.servers):
            if method in capability_map:
                capability = capability_map[method]
                if capability in server.capabilities:
                    supporting_servers.append(i)

        self.logger.debug(f"find_server_for_request({method}) = {supporting_servers}")
        return supporting_servers

    async def handle_client_message(self, message: dict) -> None:
        method = message.get('method')
        msg_id = message.get('id')
        self.logger.debug(f"CCC>>>: got message {msg_id}, {method}: {str(message)[:128]}")
        
        server_indices = self.find_server_for_request(method)
        
        if method == 'initialize':
            if msg_id:
                self.request_sources[msg_id] = ('initialize', set(server_indices))
            
            for i in server_indices:
                server = self.servers[i]
                if server.writer:
                    self.logger.debug(f">>>SSS{i}: Writing initialize message {msg_id}: {str(message)[:64]}")
                    await self.write_message(server.writer, message)
                    
        elif method == 'initialized':
            for i in server_indices:
                server = self.servers[i]
                if server.writer:
                    self.logger.debug(f">>>SSS{i}: Writing initialized message {msg_id}: {str(message)[:64]}")
                    await self.write_message(server.writer, message)
                    server.initialized = True
                    
        else:
            if msg_id:
                self.request_sources[msg_id] = ('request', set())
            for i in server_indices:
                server = self.servers[i]
                if server.writer:
                    if msg_id:
                        self.request_sources[msg_id][1].add(i)
                    self.logger.debug(f">>>SSS{i}: Writing {method or ''} message {msg_id}: {str(message)[:64]}")
                    await self.write_message(server.writer, message)

    async def handle_server_response(self, message: dict, server_index: int, client_writer: asyncio.StreamWriter) -> None:
        msg_id = message.get('id')
        method = message.get('method', '')
        
        self.logger.debug(f"Handling response from server {server_index}, msg {msg_id}, method {method}, expecting {self.request_sources}")
        if msg_id in self.request_sources:
            req_type, req_servers = self.request_sources[msg_id]
            self.logger.debug(f"<<<SSS{server_index}: got expected {method} msg {msg_id}: \"{str(message)[:128]}...\"")

            if req_type == 'initialize':
                capabilities = message.get('result', {}).get('capabilities', {})
                self.servers[server_index].capabilities = capabilities
                self.logger.info(f"Server {server_index} capabilities:\n{json.dumps(capabilities, indent=2, sort_keys=True)}")

                # If this was the last server to respond, send merged capabilities
                req_servers.remove(server_index)
                if not req_servers:  # empty set
                    message['result']['capabilities'] = self.merge_capabilities()
                    await self.write_message(client_writer, message)
                    del self.request_sources[msg_id]
                    
            elif req_type == 'request' and server_index in set(req_servers):
                self.logger.debug(f"CCC<<<: Passing {method} reply {msg_id}: {str(message)[:64]}")
                await self.write_message(client_writer, message)
                req_servers.remove(server_index)
                if not req_servers:
                    del self.request_sources[msg_id]
            else:
                self.logger.error(f"SERVER: DROPPING {req_type} message {msg_id} from {server_index} to client: {str(message)[:64]}")
                if server_index in req_servers:
                    req_servers.remove(server_index)
                if not req_servers:
                    del self.request_sources[msg_id]
                
        else:
            self.logger.debug(f"CCC<<<SSS{server_index}: passing {method} message {msg_id or ''}: {str(message)[:64]}")
            await self.write_message(client_writer, message)

    async def handle_client_stream(self, reader: asyncio.StreamReader) -> None:
        while True:
            message = await self.read_message(reader)
            if not message:
                break
            await self.handle_client_message(message)

    async def handle_server_stream(self, server_index: int, client_writer: asyncio.StreamWriter) -> None:
        server = self.servers[server_index]
        if not server.reader:
            return
            
        try:
            while True:
                # Create tasks for both streams
                message_task = asyncio.create_task(self.read_message(server.reader))
                tasks = [message_task]
                if server.stderr_reader:
                    stderr_task = asyncio.create_task(server.stderr_reader.readline())
                    tasks = [message_task, stderr_task]
                
                # Wait for either task to complete
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

                # Cancel the pending task
                for task in pending:
                    task.cancel()

                # Handle whichever task completed
                for task in done:
                    try:
                        result = await task
                        if task == message_task:
                            if result is None:
                                self.logger.error(f"Server {server_index} has died.")
                                return
                            if not result:
                                return
                            await self.handle_server_response(result, server_index, client_writer)
                        else:  # stderr_task
                            assert(isinstance(result, bytes))
                            stderr_line = result.decode('utf-8').rstrip()
                            if stderr_line:
                                self.logger.error(f"stderr {server_index}: {stderr_line}")
                    except Exception as e:
                        self.logger.error(f"Error handling server {server_index} I/O: {e}\n{traceback.format_exc()}")
        except Exception as e:
            self.logger.error(f"Server {server_index} connection error: {str(e)}\n{traceback.format_exc()}")

    async def handle_client_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            client_task = asyncio.create_task(self.handle_client_stream(reader))
            server_tasks = [
                asyncio.create_task(self.handle_server_stream(i, writer))
                for i in range(len(self.servers))
            ]
            
            await client_task
            
            for task in server_tasks:
                task.cancel()
                
        except Exception as e:
            self.logger.error(f"Error in client connection: {e}")
        finally:
            writer.close()
            await writer.wait_closed()

    async def serve_stdio(self) -> None:
        await self.start_servers()

        try:
            loop = asyncio.get_event_loop()
            reader = asyncio.StreamReader()
            protocol = asyncio.StreamReaderProtocol(reader)
            await loop.connect_read_pipe(lambda: protocol, sys.stdin)

            # Create a proper protocol for writing
            write_transport, _ = await loop.connect_write_pipe(
                asyncio.streams.FlowControlMixin,  # Use this instead of BaseProtocol
                sys.stdout.buffer
            )
            writer = asyncio.StreamWriter(write_transport, protocol, reader, loop)

            await self.handle_client_connection(reader, writer)
        finally:
            await self.cleanup()

    async def serve_tcp(self, host: str = '127.0.0.1', port: int = 8888) -> None:
        await self.start_servers()
        
        server = await asyncio.start_server(
            self.handle_client_connection, host, port
        )
        
        self.logger.info(f"LSP Multiplexer listening on {host}:{port}")
        async with server:
            await server.serve_forever()

    async def cleanup(self) -> None:
        cleanup_tasks = [server.cleanup() for server in self.servers]
        await asyncio.gather(*cleanup_tasks)

async def main() -> None:
    parser = argparse.ArgumentParser(description='LSP Multiplexer')
    parser.add_argument('--stdio', action='store_true', help='Use stdio instead of TCP')
    parser.add_argument('--host', default='127.0.0.1', help='Host to listen on (default: 127.0.0.1)')
    parser.add_argument('--port', type=int, default=8888, help='Port to listen on (default: 8888)')
    parser.add_argument('--log-level', 
                        default='INFO',
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
                        help='Set the logging level (default: INFO)')
    args = parser.parse_args()

    # Logging setup
    SUPPORTS_COLOR = hasattr(sys.stderr, 'isatty') and sys.stderr.isatty()
    RED = '\033[91m' if SUPPORTS_COLOR else ''
    RESET = '\033[0m' if SUPPORTS_COLOR else ''
    class ColorFormatter(logging.Formatter):
        def format(self, record):
            if record.levelno == logging.ERROR:
                record.msg = f"{RED}{record.msg}{RESET}"
            return super().format(record)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(ColorFormatter('%(asctime)s - %(levelname)s - %(message)s'))

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[handler]
    )


    multiplexer = LSPMultiplexer([
        ["uv", "run", "/Users/garyo/src/test-langserver/test-server.py"],
        ["pyright-langserver", "--stdio"],
        #"tcp://localhost:8080",
    ])
    
    try:
        if args.stdio:
            await multiplexer.serve_stdio()
        else:
            await multiplexer.serve_tcp(args.host, args.port)
    finally:
        await multiplexer.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
