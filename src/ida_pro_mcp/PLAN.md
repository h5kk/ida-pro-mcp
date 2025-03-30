# IDA Pro MCP Server Enhancement Plan

## Objective

Implement additional IDA Pro tools into the existing MCP server project, organize them into a separate file, and update the server documentation.

## Steps

1.  [x] Create this `PLAN.md` file.
2.  [x] Understand the project architecture: Tools are implemented in `mcp-plugin.py` and wrapped by `server.py`. `ida_tools.py` is not needed.
3.  [x] Implement the following tools in `mcp-plugin.py`, decorating with `@jsonrpc` and `@idaread`/`@idawrite`, using the provided code snippets:
    *   [x] `get_bytes`
    *   [x] `get_disasm`
    *   [x] `get_decompiled_func`
    *   [x] `get_function_name`
    *   [x] `get_segments`
    *   [x] `get_functions`
    *   [x] `get_xrefs_to` (Verified existing)
    *   [x] `get_imports`
    *   [x] `get_exports`
    *   [x] `get_entry_point`
    *   [x] `make_function`
    *   [x] `undefine_function`
    *   [x] `get_dword_at`
    *   [x] `get_word_at`
    *   [x] `get_byte_at`
    *   [x] `get_qword_at`
    *   [x] `get_float_at`
    *   [x] `get_double_at`
    *   [x] `get_string_at`
    *   [x] `get_strings`
    *   [x] `get_string_list`
    *   [x] `get_current_file_path`
    *   [x] `list_files_with_relative_path`
    *   [x] `read_file`
    *   [x] `write_file`
    *   [x] `read_binary`
    *   [x] `write_binary`
    *   [x] `eval_python`
    *   [x] `get_instruction_length`
4.  [x] Add necessary imports (`ida_bytes`, `ida_funcs`, `ida_ida`, `ida_ua`, `ida_segment`, `ida_nalt`, `ida_xref`, `ida_entry`, `ida_name`, `os`, `glob`, `json`, `typing`, `struct`) to `mcp-plugin.py`.
5.  [x] Add the `binary_analysis_strategy` prompt function (from user request) to `server.py`, decorating it with `@mcp.prompt()`.
6.  [x] Ask user for implementation logic for missing tools.
7.  [x] Implement remaining tools in `mcp-plugin.py` once logic is provided.
8.  [x] Verify `requirements.txt` is up-to-date. (File not found, assumed no changes needed).
9.  [ ] Review and test the changes (requires running `server.py` to regenerate wrappers).
