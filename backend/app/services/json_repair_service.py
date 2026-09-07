import json
import re
from typing import Optional, Tuple, Any, Dict, List

def strip_markdown_fences(text: str) -> str:
    """
    Remove surrounding Markdown code fences if present,
    handling ```json ... ```, ``` ... ```, and text with embedded code blocks.
    """
    if not text:
        return ""
    
    cleaned = text.strip()
    
    # Check if the entire string is wrapped in markdown code fence
    fence_pattern = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```$", re.DOTALL | re.IGNORECASE)
    match = fence_pattern.match(cleaned)
    if match:
        return match.group(1).strip()
    
    # Check if there is an embedded code fence inside other text
    embedded_pattern = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)
    matches = embedded_pattern.findall(cleaned)
    if matches:
        # Return the largest code fence block found
        return max(matches, key=len).strip()
    
    # If starts with ``` but might be missing closing fence
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove first line if it starts with ```
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
        
    return cleaned


def extract_json_object(text: str) -> Optional[str]:
    """
    Extract the outermost balanced JSON object { ... } from text,
    correctly skipping string literals, escapes, and nested braces.
    """
    if not text:
        return None
    
    start_idx = text.find("{")
    if start_idx == -1:
        return None
    
    depth = 0
    in_string = False
    escape = False
    
    for i in range(start_idx, len(text)):
        char = text[i]
        
        if escape:
            escape = False
            continue
            
        if char == "\\":
            if in_string:
                escape = True
            continue
            
        if char == '"':
            in_string = not in_string
            continue
            
        if not in_string:
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start_idx:i + 1]
                    
    # If closing brace was not found, return text from start_idx
    return text[start_idx:].strip()


def parse_json(text: str) -> Tuple[bool, Optional[Any], Optional[json.JSONDecodeError]]:
    """Attempt standard json.loads."""
    try:
        data = json.loads(text)
        return True, data, None
    except json.JSONDecodeError as exc:
        return False, None, exc


def _remove_trailing_commas(text: str) -> str:
    """
    Remove trailing commas before closing braces } or brackets ]
    outside of quoted string literals.
    """
    result = []
    in_string = False
    escape = False
    i = 0
    n = len(text)
    
    while i < n:
        char = text[i]
        
        if escape:
            result.append(char)
            escape = False
            i += 1
            continue
            
        if char == "\\":
            result.append(char)
            if in_string:
                escape = True
            i += 1
            continue
            
        if char == '"':
            in_string = not in_string
            result.append(char)
            i += 1
            continue
            
        if not in_string and char == ",":
            # Look ahead for next non-whitespace character
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "]}":
                # Trailing comma! Skip this comma
                i += 1
                continue
                
        result.append(char)
        i += 1
        
    return "".join(result)


def _fix_unescaped_backslashes(text: str) -> str:
    r"""
    Fix invalid backslash escapes inside JSON strings, such as Windows file paths:
    e.g. C:\Users\Mohak -> C:\\Users\\Mohak
    Valid JSON escapes: \", \\, \/, \b, \f, \n, \r, \t, \uXXXX
    """
    result = []
    in_string = False
    i = 0
    n = len(text)
    
    while i < n:
        char = text[i]
        
        if not in_string:
            if char == '"':
                in_string = True
            result.append(char)
            i += 1
            continue
            
        # Inside string
        if char == '"':
            in_string = False
            result.append(char)
            i += 1
            continue
            
        if char == "\\":
            # Check the next character
            if i + 1 < n:
                next_char = text[i + 1]
                if next_char in '"\\/bfnrt':
                    # Valid standard escape
                    result.append(char)
                    result.append(next_char)
                    i += 2
                    continue
                elif next_char == 'u':
                    # Check for 4 hex digits
                    if i + 5 < n and all(c in '0123456789abcdefABCDEF' for c in text[i + 2:i + 6]):
                        result.append(text[i:i + 6])
                        i += 6
                        continue
            # Invalid escape or trailing backslash -> double escape it
            result.append("\\\\")
            i += 1
            continue
            
        result.append(char)
        i += 1
        
    return "".join(result)


def _balance_unclosed_structures(text: str) -> str:
    """
    If the JSON string was truncated and has unclosed quotes, brackets, or braces,
    close any open string and append matching closing brackets/braces in reverse order.
    """
    stack: List[str] = []
    in_string = False
    escape = False
    
    for char in text:
        if escape:
            escape = False
            continue
        if char == "\\":
            if in_string:
                escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if not in_string:
            if char in "{[":
                stack.append(char)
            elif char == "}":
                if stack and stack[-1] == "{":
                    stack.pop()
            elif char == "]":
                if stack and stack[-1] == "[":
                    stack.pop()
                    
    fixed = text
    # If string was left open, close it
    if in_string:
        fixed += '"'
        
    # Remove any trailing comma that might sit before our closing brackets
    fixed = _remove_trailing_commas(fixed)
    
    # Close structures in reverse order
    closing_map = {"{": "}", "[": "]"}
    for opener in reversed(stack):
        fixed += closing_map.get(opener, "")
        
    return fixed


def repair_json_deterministically(raw_text: str) -> Tuple[bool, Optional[Any], Optional[str], Optional[Dict[str, Any]]]:
    """
    Safely attempt deterministic JSON repair without changing graph semantics.
    Returns:
        (success, parsed_data, repair_strategy_applied, error_info)
    """
    if not raw_text or not raw_text.strip():
        return False, None, None, {"error": "Empty content"}
        
    # Initial parse attempt
    ok, data, err = parse_json(raw_text)
    if ok:
        return True, data, "NO_REPAIR_NEEDED", None
        
    first_error_info = {
        "error_type": type(err).__name__,
        "message": str(err),
        "line": err.lineno if err else None,
        "column": err.colno if err else None
    }
    
    # Strategy 1: Strip markdown fences
    candidate_1 = strip_markdown_fences(raw_text)
    if candidate_1 != raw_text:
        ok, data, err = parse_json(candidate_1)
        if ok:
            return True, data, "STRIP_MARKDOWN_FENCES", first_error_info
    else:
        candidate_1 = raw_text.strip()
        
    # Strategy 2: Extract JSON object from surrounding text
    candidate_2 = extract_json_object(candidate_1)
    if candidate_2:
        ok, data, err = parse_json(candidate_2)
        if ok:
            return True, data, "EXTRACT_JSON_OBJECT", first_error_info
    else:
        candidate_2 = candidate_1
        
    # Strategy 3: Remove trailing commas
    candidate_3 = _remove_trailing_commas(candidate_2)
    ok, data, err = parse_json(candidate_3)
    if ok:
        return True, data, "REMOVE_TRAILING_COMMAS", first_error_info
        
    # Strategy 4: Fix unescaped backslashes (e.g. Windows file paths)
    candidate_4 = _fix_unescaped_backslashes(candidate_3)
    ok, data, err = parse_json(candidate_4)
    if ok:
        return True, data, "FIX_UNESCAPED_BACKSLASHES", first_error_info
        
    # Strategy 5: Fix unescaped backslashes on candidate_2 directly
    candidate_4b = _fix_unescaped_backslashes(candidate_2)
    ok, data, err = parse_json(candidate_4b)
    if ok:
        return True, data, "FIX_UNESCAPED_BACKSLASHES_RAW", first_error_info
        
    # Strategy 6: Balance unclosed structures (truncated generation)
    candidate_5 = _balance_unclosed_structures(candidate_4)
    ok, data, err = parse_json(candidate_5)
    if ok:
        return True, data, "BALANCE_UNCLOSED_STRUCTURES", first_error_info
        
    # Strategy 7: Try balance on candidate_2 directly
    candidate_6 = _balance_unclosed_structures(candidate_2)
    ok, data, err = parse_json(candidate_6)
    if ok:
        return True, data, "BALANCE_UNCLOSED_STRUCTURES_RAW", first_error_info

    final_error_info = {
        "first_error": first_error_info,
        "last_error": {
            "error_type": type(err).__name__,
            "message": str(err),
            "line": err.lineno if err else None,
            "column": err.colno if err else None
        }
    }
    return False, None, None, final_error_info
