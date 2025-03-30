import os
import sys
import ast
import json
import shutil
import argparse
import http.client

from fastmcp import FastMCP

# The log_level is necessary for Cline to work: https://github.com/jlowin/fastmcp/issues/81
mcp = FastMCP("github.com/mrexodia/ida-pro-mcp", log_level="ERROR")

jsonrpc_request_id = 1

def make_jsonrpc_request(method: str, *params):
    """Make a JSON-RPC request to the IDA plugin"""
    global jsonrpc_request_id
    conn = http.client.HTTPConnection("localhost", 13337)
    request = {
        "jsonrpc": "2.0",
        "method": method,
        "params": list(params),
        "id": jsonrpc_request_id,
    }
    jsonrpc_request_id += 1

    try:
        conn.request("POST", "/mcp", json.dumps(request), {
            "Content-Type": "application/json"
        })
        response = conn.getresponse()
        data = json.loads(response.read().decode())

        if "error" in data:
            error = data["error"]
            code = error["code"]
            message = error["message"]
            pretty = f"JSON-RPC error {code}: {message}"
            if "data" in error:
                pretty += "\n" + error["data"]
            raise Exception(pretty)

        result = data["result"]
        # NOTE: LLMs do not respond well to empty responses
        if result is None:
            result = "success"
        return result
    except Exception:
        raise
    finally:
        conn.close()

@mcp.tool()
def check_connection() -> str:
    """Check if the IDA plugin is running"""
    try:
        metadata = make_jsonrpc_request("get_metadata")
        return f"Successfully connected to IDA Pro (open file: {metadata['module']})"
    except Exception as e:
        if sys.platform == "darwin":
            shortcut = "Ctrl+Option+M"
        else:
            shortcut = "Ctrl+Alt+M"
        return f"Failed to connect to IDA Pro! Did you run Edit -> Plugins -> MCP ({shortcut}) to start the server?"

# Code taken from https://github.com/mrexodia/ida-pro-mcp (MIT License)
class MCPVisitor(ast.NodeVisitor):
    def __init__(self):
        self.types: dict[str, ast.ClassDef] = {}
        self.functions: dict[str, ast.FunctionDef] = {}
        self.descriptions: dict[str, str] = {}

    def visit_FunctionDef(self, node):
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Name):
                if decorator.id == "jsonrpc":
                    for i, arg in enumerate(node.args.args):
                        arg_name = arg.arg
                        arg_type = arg.annotation
                        if arg_type is None:
                            raise Exception(f"Missing argument type for {node.name}.{arg_name}")
                        if isinstance(arg_type, ast.Subscript):
                            assert isinstance(arg_type.value, ast.Name)
                            assert arg_type.value.id == "Annotated"
                            assert isinstance(arg_type.slice, ast.Tuple)
                            assert len(arg_type.slice.elts) == 2
                            annot_type = arg_type.slice.elts[0]
                            annot_description = arg_type.slice.elts[1]
                            assert isinstance(annot_description, ast.Constant)
                            node.args.args[i].annotation = ast.Subscript(
                                value=ast.Name(id="Annotated", ctx=ast.Load()),
                                slice=ast.Tuple(
                                    elts=[
                                    annot_type,
                                    ast.Call(
                                        func=ast.Name(id="Field", ctx=ast.Load()),
                                        args=[],
                                        keywords=[
                                        ast.keyword(
                                            arg="description",
                                            value=annot_description)])],
                                    ctx=ast.Load()),
                                ctx=ast.Load())
                        elif isinstance(arg_type, ast.Name):
                            pass
                        else:
                            raise Exception(f"Unexpected type annotation for {node.name}.{arg_name} -> {type(arg_type)}")

                    body_comment = node.body[0]
                    if isinstance(body_comment, ast.Expr) and isinstance(body_comment.value, ast.Constant):
                        new_body = [body_comment]
                        self.descriptions[node.name] = body_comment.value.value
                    else:
                        new_body = []

                    call_args = [ast.Constant(value=node.name)]
                    for arg in node.args.args:
                        call_args.append(ast.Name(id=arg.arg, ctx=ast.Load()))
                    new_body.append(ast.Return(
                        value=ast.Call(
                            func=ast.Name(id="make_jsonrpc_request", ctx=ast.Load()),
                            args=call_args,
                            keywords=[])))
                    decorator_list = [
                        ast.Call(
                            func=ast.Attribute(
                                value=ast.Name(id="mcp", ctx=ast.Load()),
                                attr="tool",
                                ctx=ast.Load()),
                            args=[],
                            keywords=[]
                        )
                    ]
                    node_nobody = ast.FunctionDef(node.name, node.args, new_body, decorator_list, node.returns, node.type_comment, lineno=node.lineno, col_offset=node.col_offset)
                    self.functions[node.name] = node_nobody

    def visit_ClassDef(self, node):
        for base in node.bases:
            if isinstance(base, ast.Name):
                if base.id == "TypedDict":
                    self.types[node.name] = node


# --- Added Prompt Start ---

@mcp.prompt()
def binary_analysis_strategy() -> str:
    """
    Guide for analyzing the binary using the available IDA Pro MCP tools.
    """
    # Fetching the list of tools dynamically might be better in the future,
    # but for now, we'll use the list provided in the request.
    # Note: This list needs to be manually updated if tools change.
    return (
        "IDA Pro MCP Server Tools and Best Practices:\n\n"
        "Tools Available:\n"
        "- check_connection: Check connectivity with the IDA plugin.\n"
        "- get_metadata: Get metadata about the current IDB (path, base, size, hashes).\n"
        "- get_function_by_name: Get function details (address, name, size) by name.\n"
        "- get_function_by_address: Get function details by address.\n"
        "- get_current_address: Get the address currently selected in IDA.\n"
        "- get_current_function: Get the function currently selected in IDA.\n"
        "- convert_number: Convert a number between decimal, hex, bytes, ASCII, binary.\n"
        "- list_functions: List all functions in the database (paginated).\n"
        "- decompile_function: Decompile a function and get pseudocode.\n"
        "- disassemble_function: Get assembly listing for a function.\n"
        "- get_xrefs_to: Get all cross-references to a given address.\n"
        "- get_entry_points: Get all defined entry points in the database.\n"
        "- set_comment: Set a comment in disassembly and pseudocode.\n"
        "- rename_local_variable: Rename a local variable within a function.\n"
        "- rename_function: Rename a function.\n"
        "- set_function_prototype: Set a function's prototype string.\n"
        "- set_local_variable_type: Set the type of a local variable.\n"
        # --- Newly Added Tools (from user request) ---
        "- get_exports: Get all exports (index, ordinal, ea, name).\n"
        "- get_entry_point: Get the main entry point address.\n"
        "- make_function: Create a function at a specified address.\n"
        "- undefine_function: Undefine a function at a specified address.\n"
        "- get_dword_at: Get the 4-byte value at an address.\n"
        "- get_word_at: Get the 2-byte value at an address.\n"
        "- get_byte_at: Get the 1-byte value at an address.\n"
        "- get_qword_at: Get the 8-byte value at an address.\n"
        "- get_float_at: Get the float value at an address.\n"
        "- get_double_at: Get the double value at an address.\n"
        "- get_string_at: Get the string starting at an address.\n"
        "- get_strings: Get all strings in the binary (with addresses).\n"
        "- get_current_file_path: Get the full path of the loaded IDB/binary.\n"
        "- list_files_with_relative_path: List files/dirs relative to the IDB location.\n"
        "- read_file: Read content of a text file relative to the IDB.\n"
        "- write_file: Write content to a text file relative to the IDB.\n"
        "- read_binary: Read content of a binary file relative to the IDB (returns hex).\n"
        "- write_binary: Write hex content to a binary file relative to the IDB.\n"
        "- eval_python: Evaluate a Python script string in IDA (Use with caution!).\n"
        "- get_instruction_length: Get the length (bytes) of the instruction at an address.\n"
        # --- Tools Still Missing Implementation ---
        # "- get_bytes: Get raw bytes at a specified address."
        # "- get_disasm: Get single line disassembly at an address."
        # "- get_decompiled_func: Get pseudocode of the function containing an address." (Similar to decompile_function?)
        # "- get_function_name: Get function name at an address." (Similar to get_function_by_address?)
        # "- get_segments: Get all segment information."
        # "- get_functions: Get all functions." (Similar to list_functions?)
        # "- get_imports: Get all imported functions."
        "\nBest Practices for Binary Analysis:\n"
        "1. Initial Reconnaissance:\n"
        "   - `get_metadata()`: Understand basic file info (hashes, size).\n"
        "   - `get_entry_point()` / `get_entry_points()`: Find where execution starts.\n"
        "   - `get_imports()`: Check imported libraries/functions for capabilities (networking, file I/O, crypto).\n"
        "   - `get_exports()`: See what functionality the binary exposes.\n"
        "   - `get_strings()`: Look for interesting text (IPs, URLs, paths, commands, keys).\n"
        "   - `get_segments()`: Understand memory layout (code, data, resources).\n"
        "2. Code Exploration:\n"
        "   - Start at entry points or interesting functions found via strings/imports.\n"
        "   - `disassemble_function()` / `get_disasm()`: Examine assembly code.\n"
        "   - `decompile_function()` / `get_decompiled_func()`: Understand logic via pseudocode (if available).\n"
        "   - `get_function_name()` / `get_function_by_address()`: Identify functions.\n"
        "   - `get_xrefs_to()`: Follow code/data flow. Find where functions/data are used.\n"
        "3. Data Analysis:\n"
        "   - `get_bytes()`: Read raw byte sequences.\n"
        "   - `get_byte_at()`, `get_word_at()`, `get_dword_at()`, `get_qword_at()`: Read specific data sizes.\n"
        "   - `get_float_at()`, `get_double_at()`: Read floating-point values.\n"
        "   - `get_string_at()`: Read specific strings if `get_strings()` missed them.\n"
        "   - `convert_number()`: Interpret numerical values.\n"
        "4. Interaction & Modification (Use with care):\n"
        "   - `set_comment()`: Add notes to disassembly/pseudocode.\n"
        "   - `rename_function()`, `rename_local_variable()`: Improve readability.\n"
        "   - `set_function_prototype()`, `set_local_variable_type()`: Define data structures and function signatures.\n"
        "   - `make_function()`, `undefine_function()`: Correct IDA's analysis if needed.\n"
        "   - `eval_python()`: Run custom scripts for complex tasks (DANGEROUS - verify script logic).\n"
        "5. Filesystem Interaction (Use with care):\n"
        "   - `get_current_file_path()`: Know the base directory.\n"
        "   - `list_files_with_relative_path()`: Explore nearby files.\n"
        "   - `read_file()`, `read_binary()`: Load external data/scripts.\n"
        "   - `write_file()`, `write_binary()`: Save analysis results or extracted data.\n"
        "General Tips:\n"
        "- Combine tools: Use `get_strings()` then `get_xrefs_to()` on interesting string addresses.\n"
        "- Use `get_current_address()` / `get_current_function()` to quickly analyze the area you're looking at in the IDA GUI.\n"
        "- Paginate large results (`list_functions`).\n"
        "- Be mindful of addresses (hex vs dec) and use `parse_address` internally or `convert_number` externally.\n"
    )

# --- Added Prompt End ---


SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
IDA_PLUGIN_PY = os.path.join(SCRIPT_DIR, "mcp-plugin.py")
GENERATED_PY = os.path.join(SCRIPT_DIR, "server_generated.py")

# NOTE: This is in the global scope on purpose
if not os.path.exists(IDA_PLUGIN_PY):
    raise RuntimeError(f"IDA plugin not found at {IDA_PLUGIN_PY} (did you move it?)")
with open(IDA_PLUGIN_PY, "r") as f:
    code = f.read()
module = ast.parse(code, IDA_PLUGIN_PY)
visitor = MCPVisitor()
visitor.visit(module)
code = """# NOTE: This file has been automatically generated, do not modify!
# Architecture based on https://github.com/mrexodia/ida-pro-mcp (MIT License)
from typing import Annotated, Optional, TypedDict, Generic, TypeVar
from pydantic import Field

T = TypeVar("T")

"""
for type in visitor.types.values():
    code += ast.unparse(type)
    code += "\n\n"
for function in visitor.functions.values():
    code += ast.unparse(function)
    code += "\n\n"
with open(GENERATED_PY, "w") as f:
    f.write(code)
exec(compile(code, GENERATED_PY, "exec"))

MCP_FUNCTIONS = ["check_connection"] + list(visitor.functions.keys())

def generate_readme():
    print("README:")
    print(f"- `check_connection`: Check if the IDA plugin is running.")
    for function in visitor.functions.values():
        signature = function.name + "("
        for i, arg in enumerate(function.args.args):
            if i > 0:
                signature += ", "
            signature += arg.arg
        signature += ")"
        description = visitor.descriptions.get(function.name, "<no description>")
        if description[-1] != ".":
            description += "."
        print(f"- `{signature}`: {description}")
    print("\nMCP Config:")
    mcp_config = {
        "mcpServers": {
            "github.com/mrexodia/ida-pro-mcp": {
            "command": "uv",
            "args": [
                "--directory",
                "c:\\MCP\\ida-pro-mcp",
                "run",
                "server.py",
                "--install-plugin"
            ],
            "timeout": 1800,
            "disabled": False,
            "autoApprove": MCP_FUNCTIONS,
            "alwaysAllow": MCP_FUNCTIONS,
            }
        }
    }
    print(json.dumps(mcp_config, indent=2))

def get_python_executable():
    """Get the path to the Python executable"""
    venv = os.environ.get("VIRTUAL_ENV")
    if venv:
        if sys.platform == "win32":
            return os.path.join(venv, "python.exe")
        else:
            return os.path.join(venv, "bin", "python3")

    for path in sys.path:
        if sys.platform == "win32":
            path = path.replace("/", "\\")

        split = path.split(os.sep)
        if split[-1].endswith(".zip"):
            path = os.path.dirname(path)
            if sys.platform == "win32":
                python_executable = os.path.join(path, "python.exe")
            else:
                python_executable = os.path.join(path, "..", "bin", "python3")
            python_executable = os.path.abspath(python_executable)

            if os.path.exists(python_executable):
                return python_executable
    return sys.executable

def install_mcp_servers(*, uninstall=False, quiet=False, env={}):
    if sys.platform == "win32":
        configs = {
            "Cline": (os.path.join(os.getenv("APPDATA"), "Code", "User", "globalStorage", "saoudrizwan.claude-dev", "settings"), "cline_mcp_settings.json"),
            "Roo Code": (os.path.join(os.getenv("APPDATA"), "Code", "User", "globalStorage", "rooveterinaryinc.roo-cline", "settings"), "mcp_settings.json"),
            "Claude": (os.path.join(os.getenv("APPDATA"), "Claude"), "claude_desktop_config.json"),
        }
    elif sys.platform == "darwin":
        configs = {
            "Cline": (os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Code", "User", "globalStorage", "saoudrizwan.claude-dev", "settings"), "cline_mcp_settings.json"),
            "Roo Code": (os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Code", "User", "globalStorage", "rooveterinaryinc.roo-cline", "settings"), "mcp_settings.json"),
            "Claude": (os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Claude"), "claude_desktop_config.json"),
        }
    else:
        print(f"Unsupported platform: {sys.platform}")
        return

    installed = 0
    for name, (config_dir, config_file) in configs.items():
        config_path = os.path.join(config_dir, config_file)
        if not os.path.exists(config_dir):
            action = "uninstall" if uninstall else "installation"
            if not quiet:
                print(f"Skipping {name} {action}\n  Config: {config_path} (not found)")
            continue
        if not os.path.exists(config_path):
            config = {}
        else:
            with open(config_path, "r") as f:
                config = json.load(f)
        if "mcpServers" not in config:
            config["mcpServers"] = {}
        mcp_servers = config["mcpServers"]
        if uninstall:
            if mcp.name not in mcp_servers:
                if not quiet:
                    print(f"Skipping {name} uninstall\n  Config: {config_path} (not installed)")
                continue
            del mcp_servers[mcp.name]
        else:
            if mcp.name in mcp_servers:
                for key, value in mcp_servers[mcp.name].get("env", {}):
                    env[key] = value
            mcp_servers[mcp.name] = {
                "command": get_python_executable(),
                "args": [
                    __file__,
                ],
                "timeout": 1800,
                "disabled": False,
                "autoApprove": MCP_FUNCTIONS,
                "alwaysAllow": MCP_FUNCTIONS,
            }
            if env:
                mcp_servers[mcp.name]["env"] = env
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)
        if not quiet:
            action = "Uninstalled" if uninstall else "Installed"
            print(f"{action} {name} MCP server (restart required)\n  Config: {config_path}")
        installed += 1
    if not uninstall and installed == 0:
        print("No MCP servers installed")

def install_ida_plugin(*, uninstall: bool = False, quiet: bool = False):
    if sys.platform == "win32":
        ida_plugin_folder = os.path.join(os.getenv("APPDATA"), "Hex-Rays", "IDA Pro", "plugins")
    else:
        ida_plugin_folder = os.path.join(os.path.expanduser("~"), ".idapro", "plugins")
    plugin_destination = os.path.join(ida_plugin_folder, "mcp-plugin.py")
    if uninstall:
        if not os.path.exists(plugin_destination):
            print(f"Skipping IDA plugin uninstall\n  Path: {plugin_destination} (not found)")
            return
        os.remove(plugin_destination)
        if not quiet:
            print(f"Uninstalled IDA plugin\n  Path: {plugin_destination}")
    else:
        # Create IDA plugins folder
        if not os.path.exists(ida_plugin_folder):
            os.makedirs(ida_plugin_folder)

        # Skip if symlink already up to date
        realpath = os.path.realpath(plugin_destination)
        if realpath == IDA_PLUGIN_PY:
            if not quiet:
                print(f"Skipping IDA plugin installation (symlink up to date)\n  Plugin: {realpath}")
        else:
            # Remove existing plugin
            if os.path.lexists(plugin_destination):
                os.remove(plugin_destination)

            # Symlink or copy the plugin
            try:
                os.symlink(IDA_PLUGIN_PY, plugin_destination)
            except OSError:
                shutil.copy(IDA_PLUGIN_PY, plugin_destination)

            if not quiet:
                print(f"Installed IDA Pro plugin (IDA restart required)\n  Plugin: {plugin_destination}")

def main():
    parser = argparse.ArgumentParser(description="IDA Pro MCP Server")
    parser.add_argument("--install", action="store_true", help="Install the MCP Server and IDA plugin")
    parser.add_argument("--uninstall", action="store_true", help="Uninstall the MCP Server and IDA plugin")
    parser.add_argument("--generate-docs", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--install-plugin", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.install and args.uninstall:
        print("Cannot install and uninstall at the same time")
        return

    if args.install:
        install_mcp_servers()
        install_ida_plugin()
        return

    if args.uninstall:
        install_mcp_servers(uninstall=True)
        install_ida_plugin(uninstall=True)
        return

    # NOTE: Developers can use this to generate the README
    if args.generate_docs:
        generate_readme()
        return

    # NOTE: This is silent for automated Cline installations
    if args.install_plugin:
        install_ida_plugin(quiet=True)

    mcp.run(transport="stdio")

if __name__ == "__main__":
    main()
