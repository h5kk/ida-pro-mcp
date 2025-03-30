import sys

if sys.version_info < (3, 11):
    raise RuntimeError("Python 3.11 or higher is required for the MCP plugin")

import json
import struct
import threading
import http.server
from urllib.parse import urlparse
from typing import Any, Callable, get_type_hints, TypedDict, Optional, Annotated, TypeVar, Generic

class JSONRPCError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        self.code = code
        self.message = message
        self.data = data

class RPCRegistry:
    def __init__(self):
        self.methods: dict[str, Callable] = {}

    def register(self, func: Callable) -> Callable:
        self.methods[func.__name__] = func
        return func

    def dispatch(self, method: str, params: Any) -> Any:
        if method not in self.methods:
            raise JSONRPCError(-32601, f"Method '{method}' not found")

        func = self.methods[method]
        hints = get_type_hints(func)

        # Remove return annotation if present
        hints.pop("return", None)

        if isinstance(params, list):
            if len(params) != len(hints):
                raise JSONRPCError(-32602, f"Invalid params: expected {len(hints)} arguments, got {len(params)}")

            # Validate and convert parameters
            converted_params = []
            for value, (param_name, expected_type) in zip(params, hints.items()):
                try:
                    if not isinstance(value, expected_type):
                        value = expected_type(value)
                    converted_params.append(value)
                except (ValueError, TypeError):
                    raise JSONRPCError(-32602, f"Invalid type for parameter '{param_name}': expected {expected_type.__name__}")

            return func(*converted_params)
        elif isinstance(params, dict):
            if set(params.keys()) != set(hints.keys()):
                raise JSONRPCError(-32602, f"Invalid params: expected {list(hints.keys())}")

            # Validate and convert parameters
            converted_params = {}
            for param_name, expected_type in hints.items():
                value = params.get(param_name)
                try:
                    if not isinstance(value, expected_type):
                        value = expected_type(value)
                    converted_params[param_name] = value
                except (ValueError, TypeError):
                    raise JSONRPCError(-32602, f"Invalid type for parameter '{param_name}': expected {expected_type.__name__}")

            return func(**converted_params)
        else:
            raise JSONRPCError(-32600, "Invalid Request: params must be array or object")

rpc_registry = RPCRegistry()

def jsonrpc(func: Callable) -> Callable:
    """Decorator to register a function as a JSON-RPC method"""
    global rpc_registry
    return rpc_registry.register(func)

class JSONRPCRequestHandler(http.server.BaseHTTPRequestHandler):
    def send_jsonrpc_error(self, code: int, message: str, id: Any = None):
        response = {
            "jsonrpc": "2.0",
            "error": {
                "code": code,
                "message": message
            }
        }
        if id is not None:
            response["id"] = id
        response_body = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(response_body))
        self.end_headers()
        self.wfile.write(response_body)

    def do_POST(self):
        global rpc_registry

        parsed_path = urlparse(self.path)
        if parsed_path.path != "/mcp":
            self.send_jsonrpc_error(-32098, "Invalid endpoint", None)
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self.send_jsonrpc_error(-32700, "Parse error: missing request body", None)
            return

        request_body = self.rfile.read(content_length)
        try:
            request = json.loads(request_body)
        except json.JSONDecodeError:
            self.send_jsonrpc_error(-32700, "Parse error: invalid JSON", None)
            return

        # Prepare the response
        response = {
            "jsonrpc": "2.0"
        }
        if request.get("id") is not None:
            response["id"] = request.get("id")

        try:
            # Basic JSON-RPC validation
            if not isinstance(request, dict):
                raise JSONRPCError(-32600, "Invalid Request")
            if request.get("jsonrpc") != "2.0":
                raise JSONRPCError(-32600, "Invalid JSON-RPC version")
            if "method" not in request:
                raise JSONRPCError(-32600, "Method not specified")

            # Dispatch the method
            result = rpc_registry.dispatch(request["method"], request.get("params", []))
            response["result"] = result

        except JSONRPCError as e:
            response["error"] = {
                "code": e.code,
                "message": e.message
            }
            if e.data is not None:
                response["error"]["data"] = e.data
        except IDAError as e:
            response["error"] = {
                "code": -32000,
                "message": e.message,
            }
        except Exception as e:
            traceback.print_exc()
            response["error"] = {
                "code": -32603,
                "message": "Internal error (please report a bug)",
                "data": traceback.format_exc(),
            }

        try:
            response_body = json.dumps(response).encode("utf-8")
        except Exception as e:
            traceback.print_exc()
            response_body = json.dumps({
                "error": {
                    "code": -32603,
                    "message": "Internal error (please report a bug)",
                    "data": traceback.format_exc(),
                }
            }).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(response_body))
        self.end_headers()
        self.wfile.write(response_body)

    def log_message(self, format, *args):
        # Suppress logging
        pass

class MCPHTTPServer(http.server.HTTPServer):
    allow_reuse_address = False

class Server:
    HOST = "localhost"
    PORT = 13337

    def __init__(self):
        self.server = None
        self.server_thread = None
        self.running = False

    def start(self):
        if self.running:
            print("[MCP] Server is already running")
            return

        self.server_thread = threading.Thread(target=self._run_server, daemon=True)
        self.running = True
        self.server_thread.start()

    def stop(self):
        if not self.running:
            return

        self.running = False
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.server_thread:
            self.server_thread.join()
            self.server = None
        print("[MCP] Server stopped")

    def _run_server(self):
        try:
            # Create server in the thread to handle binding
            self.server = MCPHTTPServer((Server.HOST, Server.PORT), JSONRPCRequestHandler)
            print(f"[MCP] Server started at http://{Server.HOST}:{Server.PORT}")
            self.server.serve_forever()
        except OSError as e:
            if e.errno == 98 or e.errno == 10048:  # Port already in use (Linux/Windows)
                print("[MCP] Error: Port 13337 is already in use")
            else:
                print(f"[MCP] Server error: {e}")
            self.running = False
        except Exception as e:
            print(f"[MCP] Server error: {e}")
        finally:
            self.running = False

# A module that helps with writing thread safe ida code.
# Based on:
# https://web.archive.org/web/20160305190440/http://www.williballenthin.com/blog/2015/09/04/idapython-synchronization-decorator/
import logging
import queue
import traceback
import functools

import ida_hexrays
import ida_kernwin
import ida_funcs
import ida_gdl
import ida_lines
import ida_idaapi
import ida_ida
import ida_ua
import ida_segment
import idc
import idaapi
import idautils
import ida_nalt
import ida_bytes
import ida_typeinf
import ida_xref
import ida_entry
import ida_name # Added
import os
import glob
from typing import List, Tuple, Dict # Added List, Tuple, Dict

class IDAError(Exception):
    def __init__(self, message: str):
        super().__init__(message)

    @property
    def message(self) -> str:
        return self.args[0]

class IDASyncError(Exception):
    pass

# Important note: Always make sure the return value from your function f is a
# copy of the data you have gotten from IDA, and not the original data.
#
# Example:
# --------
#
# Do this:
#
#   @idaread
#   def ts_Functions():
#       return list(idautils.Functions())
#
# Don't do this:
#
#   @idaread
#   def ts_Functions():
#       return idautils.Functions()
#

logger = logging.getLogger(__name__)

# Enum for safety modes. Higher means safer:
class IDASafety:
    ida_kernwin.MFF_READ
    SAFE_NONE = ida_kernwin.MFF_FAST
    SAFE_READ = ida_kernwin.MFF_READ
    SAFE_WRITE = ida_kernwin.MFF_WRITE

call_stack = queue.LifoQueue()

def sync_wrapper(ff, safety_mode: IDASafety):
    """
    Call a function ff with a specific IDA safety_mode.
    """
    #logger.debug('sync_wrapper: {}, {}'.format(ff.__name__, safety_mode))

    if safety_mode not in [IDASafety.SAFE_READ, IDASafety.SAFE_WRITE]:
        error_str = 'Invalid safety mode {} over function {}'\
                .format(safety_mode, ff.__name__)
        logger.error(error_str)
        raise IDASyncError(error_str)

    # No safety level is set up:
    res_container = queue.Queue()

    def runned():
        #logger.debug('Inside runned')

        # Make sure that we are not already inside a sync_wrapper:
        if not call_stack.empty():
            last_func_name = call_stack.get()
            error_str = ('Call stack is not empty while calling the '
                'function {} from {}').format(ff.__name__, last_func_name)
            #logger.error(error_str)
            raise IDASyncError(error_str)

        call_stack.put((ff.__name__))
        try:
            res_container.put(ff())
        except Exception as x:
            res_container.put(x)
        finally:
            call_stack.get()
            #logger.debug('Finished runned')

    ret_val = idaapi.execute_sync(runned, safety_mode)
    res = res_container.get()
    if isinstance(res, Exception):
        raise res
    return res

def idawrite(f):
    """
    decorator for marking a function as modifying the IDB.
    schedules a request to be made in the main IDA loop to avoid IDB corruption.
    """
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        ff = functools.partial(f, *args, **kwargs)
        ff.__name__ = f.__name__
        return sync_wrapper(ff, idaapi.MFF_WRITE)
    return wrapper

def idaread(f):
    """
    decorator for marking a function as reading from the IDB.
    schedules a request to be made in the main IDA loop to avoid
      inconsistent results.
    MFF_READ constant via: http://www.openrce.org/forums/posts/1827
    """
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        ff = functools.partial(f, *args, **kwargs)
        ff.__name__ = f.__name__
        return sync_wrapper(ff, idaapi.MFF_READ)
    return wrapper

def is_window_active():
    """Returns whether IDA is currently active"""
    try:
        from PyQt5.QtWidgets import QApplication
    except ImportError:
        return False

    app = QApplication.instance()
    if app is None:
        return False

    for widget in app.topLevelWidgets():
        if widget.isActiveWindow():
            return True
    return False

class Metadata(TypedDict):
    path: str
    module: str
    base: str
    size: str
    md5: str
    sha256: str
    crc32: str
    filesize: str

def get_image_size():
    try:
        # https://www.hex-rays.com/products/ida/support/sdkdoc/structidainfo.html
        info = idaapi.get_inf_structure()
        omin_ea = info.omin_ea
        omax_ea = info.omax_ea
    except AttributeError:
        import ida_ida
        omin_ea = ida_ida.inf_get_omin_ea()
        omax_ea = ida_ida.inf_get_omax_ea()
    # Bad heuristic for image size (bad if the relocations are the last section)
    image_size = omax_ea - omin_ea
    # Try to extract it from the PE header
    header = idautils.peutils_t().header()
    if header and header[:4] == b"PE\0\0":
        image_size = struct.unpack("<I", header[0x50:0x54])[0]
    return image_size

@jsonrpc
@idaread
def get_metadata() -> Metadata:
    """Get metadata about the current IDB"""
    return {
        "path": idaapi.get_input_file_path(),
        "module": idaapi.get_root_filename(),
        "base": hex(idaapi.get_imagebase()),
        "size": hex(get_image_size()),
        "md5": ida_nalt.retrieve_input_file_md5().hex(),
        "sha256": ida_nalt.retrieve_input_file_sha256().hex(),
        "crc32": hex(ida_nalt.retrieve_input_file_crc32()),
        "filesize": hex(ida_nalt.retrieve_input_file_size()),
    }

def get_prototype(fn: ida_funcs.func_t) -> Optional[str]:
    try:
        prototype: ida_typeinf.tinfo_t = fn.get_prototype()
        if prototype is not None:
            return str(prototype)
        else:
            return None
    except AttributeError:
        try:
            return idc.get_type(fn.start_ea)
        except:
            tif = ida_typeinf.tinfo_t()
            if ida_nalt.get_tinfo(tif, fn.start_ea):
                return str(tif)
            return None
    except Exception as e:
        print(f"Error getting function prototype: {e}")
        return None

class Function(TypedDict):
    address: str
    name: str
    size: str

def parse_address(address: str) -> int:
    try:
        return int(address, 0)
    except ValueError:
        for ch in address:
            if ch not in "0123456789abcdefABCDEF":
                raise IDAError(f"Failed to parse address: {address}")
        raise IDAError(f"Failed to parse address (missing 0x prefix): {address}")

def get_function(address: int, *, raise_error=True) -> Function:
    fn = idaapi.get_func(address)
    if fn is None:
        if raise_error:
            raise IDAError(f"No function found at address {hex(address)}")
        return None

    try:
        name = fn.get_name()
    except AttributeError:
        name = ida_funcs.get_func_name(fn.start_ea)
    return {
        "address": hex(fn.start_ea),
        "name": name,
        "size": hex(fn.end_ea - fn.start_ea),
    }

DEMANGLED_TO_EA = {}

def create_demangled_to_ea_map():
    for ea in idautils.Functions():
        # Get the function name and demangle it
        # MNG_NODEFINIT inhibits everything except the main name
        # where default demangling adds the function signature
        # and decorators (if any)
        demangled = idaapi.demangle_name(
            idc.get_name(ea, 0), idaapi.MNG_NODEFINIT)
        if demangled:
            DEMANGLED_TO_EA[demangled] = ea

@jsonrpc
@idaread
def get_function_by_name(
    name: Annotated[str, "Name of the function to get"]
) -> Function:
    """Get a function by its name"""
    function_address = idaapi.get_name_ea(idaapi.BADADDR, name)
    if function_address == idaapi.BADADDR:
        # If map has not been created yet, create it
        if len(DEMANGLED_TO_EA) == 0:
            create_demangled_to_ea_map()
        # Try to find the function in the map, else raise an error
        if name in DEMANGLED_TO_EA:
            function_address = DEMANGLED_TO_EA[name]
        else:
            raise IDAError(f"No function found with name {name}")
    return get_function(function_address)

@jsonrpc
@idaread
def get_function_by_address(
    address: Annotated[str, "Address of the function to get"]
) -> Function:
    """Get a function by its address"""
    return get_function(parse_address(address))

@jsonrpc
@idaread
def get_current_address() -> str:
    """Get the address currently selected by the user"""
    return hex(idaapi.get_screen_ea())

@jsonrpc
@idaread
def get_current_function() -> Optional[Function]:
    """Get the function currently selected by the user"""
    return get_function(idaapi.get_screen_ea())

class ConvertedNumber(TypedDict):
    decimal: str
    hexadecimal: str
    bytes: str
    ascii: Optional[str]
    binary: str

@jsonrpc
def convert_number(
    text: Annotated[str, "Textual representation of the number to convert"],
    size: Annotated[Optional[int], "Size of the variable in bytes"],
) -> ConvertedNumber:
    """Convert a number (decimal, hexadecimal) to different representations"""
    try:
        value = int(text, 0)
    except ValueError:
        raise IDAError(f"Invalid number: {text}")

    # Estimate the size of the number
    if not size:
        size = 0
        n = abs(value)
        while n:
            size += 1
            n >>= 1
        size += 7
        size //= 8

    # Convert the number to bytes
    try:
        bytes = value.to_bytes(size, "little", signed=True)
    except OverflowError:
        raise IDAError(f"Number {text} is too big for {size} bytes")

    # Convert the bytes to ASCII
    ascii = ""
    for byte in bytes.rstrip(b"\x00"):
        if byte >= 32 and byte <= 126:
            ascii += chr(byte)
        else:
            ascii = None
            break

    return {
        "decimal": str(value),
        "hexadecimal": hex(value),
        "bytes": bytes.hex(" "),
        "ascii": ascii,
        "binary": bin(value)
    }

T = TypeVar("T")

class Page(TypedDict, Generic[T]):
    data: list[T]
    next_offset: Optional[int]

def paginate(data: list[T], offset: int, count: int) -> Page[T]:
    if count == 0:
        count = len(data)
    next_offset = offset + count
    if next_offset >= len(data):
        next_offset = None
    return {
        "data": data[offset:offset+count],
        "next_offset": next_offset,
    }

@jsonrpc
@idaread
def list_functions(
    offset: Annotated[int, "Offset to start listing from (start at 0)"],
    count: Annotated[int, "Number of functions to list (100 is a good default, 0 means remainder)"],
) -> Page[Function]:
    """List all functions in the database (paginated)"""
    functions = [get_function(address) for address in idautils.Functions()]
    return paginate(functions, offset, count)

def decompile_checked(address: int) -> ida_hexrays.cfunc_t:
    if not ida_hexrays.init_hexrays_plugin():
        raise IDAError("Hex-Rays decompiler is not available")
    error = ida_hexrays.hexrays_failure_t()
    cfunc: ida_hexrays.cfunc_t = ida_hexrays.decompile_func(address, error, ida_hexrays.DECOMP_WARNINGS)
    if not cfunc:
        message = f"Decompilation failed at {hex(address)}"
        if error.str:
            message += f": {error.str}"
        if error.errea != idaapi.BADADDR:
            message += f" (address: {hex(error.errea)})"
        raise IDAError(message)
    return cfunc

@jsonrpc
@idaread
def decompile_function(
    address: Annotated[str, "Address of the function to decompile"]
) -> str:
    """Decompile a function at the given address"""
    address = parse_address(address)
    cfunc = decompile_checked(address)
    if is_window_active():
        ida_hexrays.open_pseudocode(address, ida_hexrays.OPF_REUSE)
    sv = cfunc.get_pseudocode()
    pseudocode = ""
    for i, sl in enumerate(sv):
        sl: ida_kernwin.simpleline_t
        item = ida_hexrays.ctree_item_t()
        addr = None if i > 0 else cfunc.entry_ea
        if cfunc.get_line_item(sl.line, 0, False, None, item, None):
            ds = item.dstr().split(": ")
            if len(ds) == 2:
                try:
                    addr = int(ds[0], 16)
                except ValueError:
                    pass
        line = ida_lines.tag_remove(sl.line)
        if len(pseudocode) > 0:
            pseudocode += "\n"
        if not addr:
            pseudocode += f"/* line: {i} */ {line}"
        else:
            pseudocode += f"/* line: {i}, address: {hex(addr)} */ {line}"

    return pseudocode

@jsonrpc
@idaread
def disassemble_function(
    start_address: Annotated[str, "Address of the function to disassemble"]
) -> str:
    """Get assembly code (address: instruction; comment) for a function"""
    start = parse_address(start_address)
    func = idaapi.get_func(start)
    if not func:
        raise IDAError(f"No function found containing address {start_address}")
    if is_window_active():
        ida_kernwin.jumpto(start)

    # TODO: add labels and limit the maximum number of instructions
    disassembly = ""
    for address in ida_funcs.func_item_iterator_t(func):
        if len(disassembly) > 0:
            disassembly += "\n"
        disassembly += f"{hex(address)}: "
        disassembly += idaapi.generate_disasm_line(address, idaapi.GENDSM_REMOVE_TAGS)
        comment = idaapi.get_cmt(address, False)
        if not comment:
            comment = idaapi.get_cmt(address, True)
        if comment:
            disassembly += f"; {comment}"
    return disassembly

class Xref(TypedDict):
    address: str
    type: str
    function: Optional[Function]

@jsonrpc
@idaread
def get_xrefs_to(
    address: Annotated[str, "Address to get cross references to"]
) -> list[Xref]:
    """Get all cross references to the given address"""
    xrefs = []
    xref: ida_xref.xrefblk_t
    for xref in idautils.XrefsTo(parse_address(address)):
        xrefs.append({
            "address": hex(xref.frm),
            "type": "code" if xref.iscode else "data",
            "function": get_function(xref.frm, raise_error=False),
        })
    return xrefs

@jsonrpc
@idaread
def get_entry_points() -> list[Function]:
    """Get all entry points in the database"""
    result = []
    for i in range(ida_entry.get_entry_qty()):
        ordinal = ida_entry.get_entry_ordinal(i)
        address = ida_entry.get_entry(ordinal)
        func = get_function(address, raise_error=False)
        if func is not None:
            result.append(func)
    return result

@jsonrpc
@idawrite
def set_comment(
    address: Annotated[str, "Address in the function to set the comment for"],
    comment: Annotated[str, "Comment text"]
):
    """Set a comment for a given address in the function disassembly and pseudocode"""
    address = parse_address(address)

    if not idaapi.set_cmt(address, comment, False):
        raise IDAError(f"Failed to set disassembly comment at {hex(address)}")

    # Reference: https://cyber.wtf/2019/03/22/using-ida-python-to-analyze-trickbot/
    # Check if the address corresponds to a line
    cfunc = decompile_checked(address)

    # Special case for function entry comments
    if address == cfunc.entry_ea:
        idc.set_func_cmt(address, comment, True)
        cfunc.refresh_func_ctext()
        return

    eamap = cfunc.get_eamap()
    if address not in eamap:
        print(f"Failed to set decompiler comment at {hex(address)}")
        return
    nearest_ea = eamap[address][0].ea

    # Remove existing orphan comments
    if cfunc.has_orphan_cmts():
        cfunc.del_orphan_cmts()
        cfunc.save_user_cmts()

    # Set the comment by trying all possible item types
    tl = idaapi.treeloc_t()
    tl.ea = nearest_ea
    for itp in range(idaapi.ITP_SEMI, idaapi.ITP_COLON):
        tl.itp = itp
        cfunc.set_user_cmt(tl, comment)
        cfunc.save_user_cmts()
        cfunc.refresh_func_ctext()
        if not cfunc.has_orphan_cmts():
            return
        cfunc.del_orphan_cmts()
        cfunc.save_user_cmts()
    print(f"Failed to set decompiler comment at {hex(address)}")

def refresh_decompiler_widget():
    widget = ida_kernwin.get_current_widget()
    if widget is not None:
        vu = ida_hexrays.get_widget_vdui(widget)
        if vu is not None:
            vu.refresh_ctext()

def refresh_decompiler_ctext(function_address: int):
    error = ida_hexrays.hexrays_failure_t()
    cfunc: ida_hexrays.cfunc_t = ida_hexrays.decompile_func(function_address, error, ida_hexrays.DECOMP_WARNINGS)
    if cfunc:
        cfunc.refresh_func_ctext()

@jsonrpc
@idawrite
def rename_local_variable(
    function_address: Annotated[str, "Address of the function containing the variable"],
    old_name: Annotated[str, "Current name of the variable"],
    new_name: Annotated[str, "New name for the variable (empty for a default name)"]
):
    """Rename a local variable in a function"""
    func = idaapi.get_func(parse_address(function_address))
    if not func:
        raise IDAError(f"No function found at address {function_address}")
    if not ida_hexrays.rename_lvar(func.start_ea, old_name, new_name):
        raise IDAError(f"Failed to rename local variable {old_name} in function {hex(func.start_ea)}")
    refresh_decompiler_ctext(func.start_ea)

@jsonrpc
@idawrite
def rename_function(
    function_address: Annotated[str, "Address of the function to rename"],
    new_name: Annotated[str, "New name for the function (empty for a default name)"]
):
    """Rename a function"""
    func = idaapi.get_func(parse_address(function_address))
    if not func:
        raise IDAError(f"No function found at address {function_address}")
    if not idaapi.set_name(func.start_ea, new_name):
        raise IDAError(f"Failed to rename function {hex(func.start_ea)} to {new_name}")
    refresh_decompiler_ctext(func.start_ea)

@jsonrpc
@idawrite
def set_function_prototype(
    function_address: Annotated[str, "Address of the function"],
    prototype: Annotated[str, "New function prototype"]
) -> str:
    """Set a function's prototype"""
    func = idaapi.get_func(parse_address(function_address))
    if not func:
        raise IDAError(f"No function found at address {function_address}")
    try:
        tif = ida_typeinf.tinfo_t(prototype, None, ida_typeinf.PT_SIL)
        if not tif.is_func():
            raise IDAError(f"Parsed declaration is not a function type")
        if not ida_typeinf.apply_tinfo(func.start_ea, tif, ida_typeinf.PT_SIL):
            raise IDAError(f"Failed to apply type")
        refresh_decompiler_ctext(func.start_ea)
    except Exception as e:
        raise IDAError(f"Failed to parse prototype string: {prototype}")

class my_modifier_t(ida_hexrays.user_lvar_modifier_t):
    def __init__(self, var_name: str, new_type: ida_typeinf.tinfo_t):
        ida_hexrays.user_lvar_modifier_t.__init__(self)
        self.var_name = var_name
        self.new_type = new_type

    def modify_lvars(self, lvars):
        for lvar_saved in lvars.lvvec:
            lvar_saved: ida_hexrays.lvar_saved_info_t
            if lvar_saved.name == self.var_name:
                lvar_saved.type = self.new_type
                return True
        return False

@jsonrpc
@idawrite
def set_local_variable_type(
    function_address: Annotated[str, "Address of the function containing the variable"],
    variable_name: Annotated[str, "Name of the variable"],
    new_type: Annotated[str, "New type for the variable"]
):
    """Set a local variable's type"""
    try:
        new_tif = ida_typeinf.tinfo_t(new_type, None, ida_typeinf.PT_SIL)
    except Exception:
        raise IDAError(f"Failed to parse type: {new_type}")
    func = idaapi.get_func(parse_address(function_address))
    if not func:
        raise IDAError(f"No function found at address {function_address}")
    if not ida_hexrays.rename_lvar(func.start_ea, variable_name, variable_name):
        raise IDAError(f"Failed to find local variable: {variable_name}")
    modifier = my_modifier_t(variable_name, new_tif)
    if not ida_hexrays.modify_user_lvars(func.start_ea, modifier):
        raise IDAError(f"Failed to modify local variable: {variable_name}")
    refresh_decompiler_ctext(func.start_ea)


# --- Added Tools Start ---

# --- Missing Implementations from User ---

@jsonrpc
@idaread
def get_bytes(ea: int, size: int) -> str:
    """Get bytes at specified address as a hex string.

    Args:
        ea: Effective address to read from
        size: Number of bytes to read
    """
    if size <= 0:
        raise IDAError("Size must be positive.")
    try:
        # Use ida_bytes.get_bytes for potentially better performance
        byte_data = ida_bytes.get_bytes(ea, size)
        if byte_data is None:
             # Check if any part of the range is unloaded
             for i in range(size):
                 if not ida_bytes.is_loaded(ea + i):
                     raise IDAError(f"Address range {hex(ea)} to {hex(ea+size-1)} contains unloaded memory.")
             # If loaded but get_bytes failed, raise generic error
             raise IDAError(f"Failed to read {size} bytes at {hex(ea)}.")
        return byte_data.hex()
    except Exception as e:
        # Catch potential exceptions from get_bytes or is_loaded
        raise IDAError(f"Error in get_bytes at {hex(ea)}: {str(e)}")


@jsonrpc
@idaread
def get_disasm(ea: int) -> str:
    """Get disassembly for the instruction at the specified address.

    Args:
        ea: Effective address to disassemble
    """
    if not ida_bytes.is_loaded(ea):
        raise IDAError(f"Address {hex(ea)} is not loaded.")
    line = idc.generate_disasm_line(ea, 0)
    if not line:
        # Check if it's the start of an instruction
        flags = ida_bytes.get_flags(ea)
        if not ida_bytes.is_code(flags):
             raise IDAError(f"Address {hex(ea)} is not code.")
        # If it is code but disasm failed, raise generic error
        raise IDAError(f"Failed to generate disassembly for address {hex(ea)}.")
    return line


@jsonrpc
@idaread
def get_decompiled_func(ea: int) -> str:
    """Get decompiled pseudocode of the function containing the address.

    Args:
        ea: Effective address within the function
    """
    func = ida_funcs.get_func(ea)
    if not func:
        raise IDAError(f"No function found containing address {hex(ea)}")

    # Reuse existing decompile_checked logic
    try:
        cfunc = decompile_checked(func.start_ea) # Decompile from function start
        sv = cfunc.get_pseudocode()
        pseudocode = "\n".join([ida_lines.tag_remove(sl.line) for sl in sv])
        return pseudocode
    except IDAError as e:
        # Propagate IDAError from decompile_checked or other issues
        raise e
    except Exception as e:
        # Catch unexpected errors during decompilation process
        raise IDAError(f"Unexpected error decompiling function at {hex(func.start_ea)}: {str(e)}")


@jsonrpc
@idaread
def get_function_name(ea: int) -> str:
    """Get the name of the function containing the specified address.

    Args:
        ea: Effective address within the function
    """
    func = ida_funcs.get_func(ea)
    if not func:
        raise IDAError(f"No function found containing address {hex(ea)}")

    # Use get_func_name for potentially more robust name retrieval
    name = ida_funcs.get_func_name(func.start_ea)
    if not name:
        # If get_func_name fails, try get_name as a fallback
        name = ida_name.get_name(func.start_ea)
        if not name:
             # It's unusual for a function start not to have a name (even default loc_...)
             # but handle it just in case.
             raise IDAError(f"Could not retrieve name for function at {hex(func.start_ea)}")
    return name


@jsonrpc
@idaread
def get_segments() -> List[Dict[str, Any]]:
    """Get all segments information."""
    segments = []
    for n in range(ida_segment.get_segm_qty()):
        seg = ida_segment.getnseg(n)
        if not seg:
            continue # Should not happen if n < get_segm_qty()

        # Use API functions to get details for robustness
        name = ida_segment.get_segm_name(seg)
        s_class = ida_segment.get_segm_class(seg)

        segments.append(
            {
                "start": hex(seg.start_ea),
                "end": hex(seg.end_ea),
                "name": name if name else "",
                "class": s_class if s_class else "",
                "perm": seg.perm, # Permissions (int)
                "bitness": seg.bitness, # 0=16, 1=32, 2=64
                "align": seg.align, # Alignment code
                "comb": seg.comb, # Combination code
                "flags": seg.flags, # Segment flags
                # 'type': seg.type, # Type is often internal, flags/perm more useful
                # 'sel': seg.sel, # Selector is usually less relevant
            }
        )
    return segments


@jsonrpc
@idaread
def get_functions() -> List[Dict[str, Any]]:
    """Get all functions in the binary (address and name)."""
    functions = []
    for func_ea in idautils.Functions():
        # Use the more robust get_func_name
        func_name = ida_funcs.get_func_name(func_ea)
        if not func_name:
             # Fallback if needed, though unlikely for function starts
             func_name = ida_name.get_name(func_ea)

        functions.append({"address": hex(func_ea), "name": func_name if func_name else f"sub_{func_ea:X}"})
    return functions


@jsonrpc
@idaread
def get_imports() -> Dict[str, List[Dict[str, Any]]]:
    """Get all imports, grouped by module.

    Returns:
        Dict where keys are module names and values are lists of import details.
        Each import detail dict contains 'address', 'name', and 'ordinal'.
    """
    import_tree = {}
    nimps = idaapi.get_import_module_qty()

    for i in range(nimps):
        mod_name = idaapi.get_import_module_name(i)
        if not mod_name:
            continue

        imports_list = []
        def imports_cb(ea, name, ord):
            imports_list.append({
                "address": hex(ea),
                "name": name if name else "",
                "ordinal": ord
            })
            return True # Continue enumeration

        idaapi.enum_import_names(i, imports_cb)

        if imports_list:
            # Ensure module name is a valid key (replace invalid chars if necessary, though unlikely)
            valid_mod_name = mod_name.replace('.', '_') # Example replacement
            if valid_mod_name not in import_tree:
                import_tree[valid_mod_name] = []
            import_tree[valid_mod_name].extend(imports_list)

    return import_tree


@jsonrpc
@idaread
def get_string_list() -> List[str]:
    """Get a list of all string contents found in the binary."""
    strings_only = []
    for s in idautils.Strings():
        try:
            str_content = s.str.decode('utf-8', errors='replace') if hasattr(s, 'str') and isinstance(s.str, bytes) else str(s)
            strings_only.append(str_content)
        except Exception:
            strings_only.append("<decoding error>")
    return strings_only

# --- End Missing Implementations ---


@jsonrpc
@idaread
def get_exports() -> List[Dict[str, Any]]:
    """Get all exports in the binary.

    @return: List of dicts {index, ordinal, address, name, forwarded_to}
    """
    exports = []
    for index, ordinal, ea, name in idautils.Entries():
         # Check for forwarded exports (optional, but useful)
         forwarded = ida_nalt.get_forwarded_export(ea)
         exports.append({
             "index": index,
             "ordinal": ordinal,
             "address": hex(ea),
             "name": name if name else "",
             "forwarded_to": forwarded if forwarded else ""
         })
    return exports


@jsonrpc
@idaread
def get_entry_point() -> str:
    """Get the main entry point address of the binary as hex."""
    try:
        # Modern IDA API
        return ida_ida.inf_get_start_ea()
    except (ImportError, AttributeError):
        try:
            # Alternative method: idc.get_inf_attr
            return idc.get_inf_attr(idc.INF_START_EA)
        except (ImportError, AttributeError):
            # Last alternative method: use cvar.inf (might be older IDA)
            entry_ea = idaapi.cvar.inf.start_ea
    if entry_ea == idaapi.BADADDR:
        raise IDAError("Could not determine entry point.")
    return hex(entry_ea)


@jsonrpc
@idawrite
def make_function(ea_str: str) -> str:
    """Make a function at specified address."""
    ea = parse_address(ea_str)
    if not ida_funcs.add_func(ea):
        # Check if already a function start
        if ida_funcs.get_func(ea) and ida_funcs.get_func(ea).start_ea == ea:
             return f"Address {hex(ea)} is already the start of a function."
        # Check if part of another function
        owner_func = ida_funcs.get_func(ea)
        if owner_func:
             raise IDAError(f"Failed to create function at {hex(ea)}. Address belongs to function {ida_funcs.get_func_name(owner_func.start_ea)} ({hex(owner_func.start_ea)}).")
        # Check if data
        flags = ida_bytes.get_flags(ea)
        if ida_bytes.is_data(flags):
             raise IDAError(f"Failed to create function at {hex(ea)}. Address is defined as data.")
        # Generic failure
        raise IDAError(f"Failed to create function at {hex(ea)}. Reason unknown.")
    return f"Successfully created function at {hex(ea)}."


@jsonrpc
@idawrite
def undefine_function(ea_str: str) -> str:
    """Undefine a function at specified address (must be start address)."""
    ea = parse_address(ea_str)
    func = ida_funcs.get_func(ea)
    # Ensure we are undefining at the start address
    if not func or func.start_ea != ea:
         raise IDAError(f"Address {hex(ea)} is not the start of a function.")

    if not ida_funcs.del_func(ea):
        raise IDAError(f"Failed to undefine function at {hex(ea)}. Reason unknown.")
    return f"Successfully undefined function at {hex(ea)}."


@jsonrpc
@idaread
def get_dword_at(ea_str: str) -> int:
    """Get the dword (4 bytes) at specified address."""
    ea = parse_address(ea_str)
    # Ensure address is valid before reading
    # Check if 4 bytes are loaded
    for i in range(4):
        if not ida_bytes.is_loaded(ea + i):
            raise IDAError(f"Address range {hex(ea)} to {hex(ea+3)} is not fully loaded.")
    # Use ida_bytes API for potentially better handling of undefined bytes
    val = ida_bytes.get_dword(ea)
    # get_dword returns BADADDR (-1) on failure, which could be a valid value.
    # Check flags explicitly if needed, but is_loaded check is primary.
    # if val == idaapi.BADADDR and ida_bytes.get_flags(ea) == ida_bytes.FF_UNDEF:
    #     raise IDAError(f"Could not read dword at {hex(ea)} (undefined bytes).")
    return val


@jsonrpc
@idaread
def get_word_at(ea_str: str) -> int:
    """Get the word (2 bytes) at specified address."""
    ea = parse_address(ea_str)
    for i in range(2):
        if not ida_bytes.is_loaded(ea + i):
            raise IDAError(f"Address range {hex(ea)} to {hex(ea+1)} is not fully loaded.")
    val = ida_bytes.get_word(ea)
    return val


@jsonrpc
@idaread
def get_byte_at(ea_str: str) -> int:
    """Get the byte (1 byte) at specified address."""
    ea = parse_address(ea_str)
    if not ida_bytes.is_loaded(ea):
        raise IDAError(f"Address {hex(ea)} is not loaded.")
    val = ida_bytes.get_byte(ea)
    return val


@jsonrpc
@idaread
def get_qword_at(ea_str: str) -> int:
    """Get the qword (8 bytes) at specified address."""
    ea = parse_address(ea_str)
    for i in range(8):
        if not ida_bytes.is_loaded(ea + i):
            raise IDAError(f"Address range {hex(ea)} to {hex(ea+7)} is not fully loaded.")
    val = ida_bytes.get_qword(ea)
    return val


@jsonrpc
@idaread
def get_float_at(ea_str: str) -> float:
    """Get the float (4 bytes) at specified address."""
    ea = parse_address(ea_str)
    for i in range(4):
        if not ida_bytes.is_loaded(ea + i):
            raise IDAError(f"Address range {hex(ea)} to {hex(ea+3)} is not fully loaded.")
    # Use ida_bytes API
    float_bytes = ida_bytes.get_bytes(ea, 4)
    if float_bytes is None:
         raise IDAError(f"Could not read bytes for float at {hex(ea)}")
    # Interpret bytes as float (assuming little-endian)
    try:
        return struct.unpack('<f', float_bytes)[0]
    except struct.error:
        raise IDAError(f"Could not unpack bytes as float at {hex(ea)}")


@jsonrpc
@idaread
def get_double_at(ea_str: str) -> float:
    """Get the double (8 bytes) at specified address."""
    ea = parse_address(ea_str)
    for i in range(8):
        if not ida_bytes.is_loaded(ea + i):
            raise IDAError(f"Address range {hex(ea)} to {hex(ea+7)} is not fully loaded.")
    double_bytes = ida_bytes.get_bytes(ea, 8)
    if double_bytes is None:
         raise IDAError(f"Could not read bytes for double at {hex(ea)}")
    try:
        return struct.unpack('<d', double_bytes)[0]
    except struct.error:
        raise IDAError(f"Could not unpack bytes as double at {hex(ea)}")


@jsonrpc
@idaread
def get_string_at(ea_str: str, maxlen: int = -1, strtype: int = ida_nalt.STRTYPE_C) -> str:
    """Get the string at specified address.

    Args:
        ea_str: Address string (e.g., "0x401000")
        maxlen: Maximum length to read (-1 for unlimited until null). Defaults to -1.
        strtype: String type code (e.g., ida_nalt.STRTYPE_C, ida_nalt.STRTYPE_PASCAL). Defaults to C string.
    """
    ea = parse_address(ea_str)
    if not ida_bytes.is_loaded(ea):
        raise IDAError(f"Address {hex(ea)} is not loaded.")

    s_bytes = ida_bytes.get_strlit_contents(ea, maxlen, strtype)
    if s_bytes is None:
        # Check if it's actually defined as a string
        flags = ida_bytes.get_flags(ea)
        if not ida_bytes.is_strlit(flags):
             raise IDAError(f"Address {hex(ea)} is not defined as a string literal.")
        # If defined but failed to read, raise generic error
        raise IDAError(f"Could not retrieve string literal contents at {hex(ea)}.")

    # Decode bytes to string, attempting common encodings
    try:
        return s_bytes.decode('utf-8')
    except UnicodeDecodeError:
        try:
            # Try latin-1 as a fallback for arbitrary byte sequences
            return s_bytes.decode('latin-1')
        except Exception:
             # If all decoding fails, return hex representation
             return f"<undecodable bytes: {s_bytes.hex()}>"
    except AttributeError: # Handle if it's already a string (older IDA?)
        return str(s_bytes)


@jsonrpc
@idaread
def get_strings(min_len: int = 5) -> List[Dict[str, Any]]:
    """Get all strings in the binary (address, content, type).

    Args:
        min_len: Minimum length of strings to retrieve. Defaults to 5.
    """
    strings_list = []
    string_types = {
        ida_nalt.STRTYPE_C: "C",
        ida_nalt.STRTYPE_PASCAL: "Pascal",
        ida_nalt.STRTYPE_LEN2: "Delphi (len2)",
        ida_nalt.STRTYPE_LEN4: "Delphi (len4)",
        ida_nalt.STRTYPE_C_16: "C (UTF-16)",
        # Add other types as needed
    }
    # Configure Strings window settings temporarily if needed (optional)
    # s_win = ida_kernwin.find_widget("Strings")
    # if s_win:
    #    ida_kernwin.activate_widget(s_win, True)
    #    # ida_kernwin.process_ui_action("StringsSetup") # This requires user interaction

    for s in idautils.Strings(minlen=min_len):
        try:
            str_content = s.str.decode('utf-8', errors='replace') if hasattr(s, 'str') and isinstance(s.str, bytes) else str(s)
        except Exception:
            str_content = "<decoding error>"
        strings_list.append({
            "address": hex(s.ea),
            "string": str_content,
            "length": s.length,
            "type": string_types.get(s.type, f"Unknown ({s.type})")
        })
    return strings_list


@jsonrpc
@idaread
def get_current_file_path() -> str:
    """Get the full path of the currently loaded input file (IDB or original)."""
    # Try IDB path first
    path = idc.get_idb_path()
    if not path:
        # Fallback to input file path
        path = idc.get_input_file_path()
        if not path:
            raise IDAError("Could not get IDB or input file path.")
    return path


def _validate_relative_path(base_dir: str, relative_path: str) -> str:
    """Helper to validate and resolve relative paths securely."""
    if not relative_path:
        raise IDAError("Relative path cannot be empty.")
    # Basic path traversal prevention
    if '..' in relative_path or ':' in relative_path or relative_path.startswith('/') or relative_path.startswith('\\'):
        raise IDAError(f"Invalid relative path: {relative_path}")

    full_path = os.path.abspath(os.path.join(base_dir, relative_path))

    # Ensure the resolved path is still within the base directory
    if not full_path.startswith(os.path.abspath(base_dir)):
        raise IDAError(f"Path traversal attempt detected: {relative_path}")

    return full_path

@jsonrpc
@idaread
def list_files_with_relative_path(relative_path: str = "") -> List[Dict[str, Any]]:
    """List files/directories in a relative path next to the IDB.

    Returns a list of dicts, each with 'name', 'path' (relative), and 'is_dir'.
    """
    # Use IDB directory as the base
    base_dir = os.path.dirname(idc.get_idb_path())
    if not base_dir:
        raise IDAError("Could not determine the IDB directory.")

    target_dir_abs = os.path.abspath(base_dir)
    if relative_path:
        try:
            target_dir_abs = _validate_relative_path(base_dir, relative_path)
            if not os.path.isdir(target_dir_abs):
                 raise IDAError(f"Relative path '{relative_path}' is not a valid directory.")
        except IDAError as e:
             raise e
        except Exception as e:
             raise IDAError(f"Error resolving relative path '{relative_path}': {str(e)}")

    results = []
    try:
        for item_name in os.listdir(target_dir_abs):
            item_abs_path = os.path.join(target_dir_abs, item_name)
            item_rel_path = os.path.join(relative_path, item_name) # Keep path relative for output
            is_dir = os.path.isdir(item_abs_path)
            results.append({
                "name": item_name,
                "path": item_rel_path.replace('\\', '/'), # Normalize path separators
                "is_dir": is_dir
            })
        return results
    except FileNotFoundError:
        raise IDAError(f"Directory not found for relative path: '{relative_path}'")
    except PermissionError:
        raise IDAError(f"Permission denied accessing directory for relative path: '{relative_path}'")
    except Exception as e:
        raise IDAError(f"Error listing directory for relative path '{relative_path}': {str(e)}")


@jsonrpc
@idaread
def read_file(relative_path: str) -> str:
    """Read the content of a text file relative to the IDB."""
    base_dir = os.path.dirname(idc.get_idb_path())
    if not base_dir:
        raise IDAError("Could not determine the IDB directory.")

    try:
        full_path = _validate_relative_path(base_dir, relative_path)
        if not os.path.isfile(full_path):
            raise IDAError(f"File not found or is not a regular file: {relative_path}")

        with open(full_path, "r", encoding='utf-8', errors='replace') as f:
            return f.read()
    except IDAError as e:
        raise e
    except FileNotFoundError:
        raise IDAError(f"File not found: {relative_path}")
    except PermissionError:
        raise IDAError(f"Permission denied reading file: {relative_path}")
    except Exception as e:
        raise IDAError(f"Error reading file '{relative_path}': {str(e)}")


@jsonrpc
@idawrite # Marked as write as it modifies the filesystem
def write_file(relative_path: str, content: str) -> str:
    """Write content to a text file relative to the IDB."""
    base_dir = os.path.dirname(idc.get_idb_path())
    if not base_dir:
        raise IDAError("Could not determine the IDB directory.")

    try:
        full_path = _validate_relative_path(base_dir, relative_path)
        # Ensure parent directory exists
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        with open(full_path, "w", encoding='utf-8') as f:
            f.write(content)
        return f"Successfully wrote to {relative_path}"
    except IDAError as e:
        raise e
    except PermissionError:
        raise IDAError(f"Permission denied writing file: {relative_path}")
    except Exception as e:
        raise IDAError(f"Error writing file '{relative_path}': {str(e)}")


@jsonrpc
@idaread
def read_binary(relative_path: str) -> str:
    """Read the content of a binary file relative to the IDB as hex."""
    base_dir = os.path.dirname(idc.get_idb_path())
    if not base_dir:
        raise IDAError("Could not determine the IDB directory.")

    try:
        full_path = _validate_relative_path(base_dir, relative_path)
        if not os.path.isfile(full_path):
            raise IDAError(f"File not found or is not a regular file: {relative_path}")

        with open(full_path, "rb") as f:
            binary_content = f.read()
        # Return as hex string for JSON compatibility
        return binary_content.hex()
    except IDAError as e:
        raise e
    except FileNotFoundError:
        raise IDAError(f"File not found: {relative_path}")
    except PermissionError:
        raise IDAError(f"Permission denied reading file: {relative_path}")
    except Exception as e:
        raise IDAError(f"Error reading binary file '{relative_path}': {str(e)}")


@jsonrpc
@idawrite # Marked as write as it modifies the filesystem
def write_binary(relative_path: str , content_hex: str) -> str:
    """Write hex-encoded content to a binary file relative to the IDB."""
    base_dir = os.path.dirname(idc.get_idb_path())
    if not base_dir:
        raise IDAError("Could not determine the IDB directory.")

    try:
        # Decode hex content back to bytes
        binary_content = bytes.fromhex(content_hex)
    except ValueError:
        raise IDAError("Invalid hex content provided.")

    try:
        full_path = _validate_relative_path(base_dir, relative_path)
        # Ensure parent directory exists
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        with open(full_path, "wb") as f:
            f.write(binary_content)
        return f"Successfully wrote binary content to {relative_path}"
    except IDAError as e:
        raise e
    except PermissionError:
        raise IDAError(f"Permission denied writing file: {relative_path}")
    except Exception as e:
        raise IDAError(f"Error writing binary file '{relative_path}': {str(e)}")


@jsonrpc
@idawrite # Marked as write as it can execute arbitrary code
def eval_python(script: str) -> Any:
    """Evaluate a Python script string within IDA's context. Use with extreme caution."""
    # WARNING: This is potentially dangerous. Only run trusted scripts.
    # Consider adding more safety checks if needed.
    try:
        # Using exec to allow statements, not just expressions
        # Provide globals/locals for context, potentially restricted
        ida_globals = {
            "idaapi": idaapi,
            "idc": idc,
            "idautils": idautils,
            "ida_bytes": ida_bytes,
            "ida_funcs": ida_funcs,
            "ida_kernwin": ida_kernwin,
            "ida_nalt": ida_nalt,
            "ida_hexrays": ida_hexrays,
            # Add other modules as needed
        }
        local_vars = {}
        exec(script, ida_globals, local_vars)
        # Attempt to return a result if the script assigns to a 'result' variable
        return local_vars.get('result', "Script executed successfully.")
    except Exception as e:
        # Capture and return the exception message for debugging
        raise IDAError(f"Error executing Python script: {str(e)}\n{traceback.format_exc()}")


@jsonrpc
@idaread
def get_instruction_length(address_str: str) -> int:
    """
    Retrieves the length (in bytes) of the instruction at the specified address. Returns 0 if invalid/undecodable.
    Args:
        address_str: The address string of the instruction.

    Returns:
        The length (in bytes) of the instruction. Returns 0 if the instruction cannot be decoded or address is invalid.
    """
    try:
        address = parse_address(address_str)
        if not ida_bytes.is_loaded(address):
             print(f"[MCP] Address {hex(address)} not loaded for get_instruction_length.")
             return 0

        insn = ida_ua.insn_t()
        length = ida_ua.decode_insn(insn, address)
        # decode_insn returns 0 if it fails or if it's not the start of an instruction
        if length == 0:
            # Check if it's actually code before printing error
            flags = ida_bytes.get_flags(address)
            if ida_bytes.is_code(flags):
                 print(f"[MCP] Failed to decode instruction at address {hex(address)}")
            # else: it might be data or undefined, returning 0 is correct.
            return 0
        return length
    except IDAError as e: # Catch parse_address errors
         print(f"[MCP] Error parsing address for get_instruction_length: {str(e)}")
         return 0
    except Exception as e:
        print(f"[MCP] Unexpected error in get_instruction_length at {address_str}: {str(e)}")
        return 0

# --- Added Tools End ---



class MCP(idaapi.plugin_t):
    flags = idaapi.PLUGIN_KEEP
    comment = "MCP Plugin"
    help = "MCP"
    wanted_name = "IDA MCP V2" # Renamed plugin
    wanted_hotkey = "Ctrl-Alt-M"

    def init(self):
        self.server = Server()
        hotkey = MCP.wanted_hotkey.replace("-", "+")
        if sys.platform == "darwin":
            hotkey = hotkey.replace("Alt", "Option")
        print(f"[MCP] Plugin loaded, use Edit -> Plugins -> MCP ({hotkey}) to start the server")
        return idaapi.PLUGIN_KEEP

    def run(self, args):
        self.server.start()

    def term(self):
        self.server.stop()

def PLUGIN_ENTRY():
    return MCP()
