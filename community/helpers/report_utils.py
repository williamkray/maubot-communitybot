"""Report generation and formatting utility functions."""

from typing import Dict, List, Any
import time


def generate_activity_report(database_results: Dict[str, List[Dict]]) -> Dict[str, Any]:
    """Generate an activity report from database results.
    
    Args:
        database_results: Dictionary containing 'warn_inactive', 'kick_inactive', 'ignored' results
        
    Returns:
        dict: Formatted activity report
    """
    report = {}
    
    # Process warn inactive users (between warn and kick thresholds)
    warn_inactive_results = database_results.get("warn_inactive", [])
    report["warn_inactive"] = [row["mxid"] for row in warn_inactive_results] or ["none"]
    
    # Process kick inactive users (beyond kick threshold)
    kick_inactive_results = database_results.get("kick_inactive", [])
    report["kick_inactive"] = [row["mxid"] for row in kick_inactive_results] or ["none"]
    
    # Process ignored users
    ignored_results = database_results.get("ignored", [])
    report["ignored"] = [row["mxid"] for row in ignored_results] or ["none"]
    
    return report


def split_doctor_report(report_text: str, max_chunk_size: int = 4000) -> List[str]:
    """Split a doctor report into chunks that fit within size limits.
    
    Args:
        report_text: The full report text
        max_chunk_size: Maximum size per chunk
        
    Returns:
        list: List of report chunks
    """
    if len(report_text) <= max_chunk_size:
        return [report_text]
    
    # Try to split by sections first
    sections = _split_by_sections(report_text, max_chunk_size)
    if sections:
        return sections
    
    # Fall back to character-based splitting
    chunks = []
    current_chunk = ""
    
    for line in report_text.split('\n'):
        if len(current_chunk) + len(line) + 1 > max_chunk_size:
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = line
            else:
                # Single line is too long, split it
                chunks.append(line[:max_chunk_size])
                current_chunk = line[max_chunk_size:]
        else:
            if current_chunk:
                current_chunk += '\n' + line
            else:
                current_chunk = line
    
    if current_chunk:
        chunks.append(current_chunk.strip())
    
    return chunks


def _split_by_sections(text: str, max_size: int) -> List[str]:
    """Split text by sections (lines starting with specific patterns).
    
    Args:
        text: The text to split
        max_size: Maximum size per section
        
    Returns:
        list: List of text sections
    """
    section_headers = ["Active users:", "Inactive users:", "Ignored users:"]
    sections = []
    current_section = ""
    
    lines = text.split('\n')
    for line in lines:
        if any(line.startswith(header) for header in section_headers):
            if current_section and len(current_section) > max_size:
                # Current section is too big, need to split it further
                return []
            if current_section:
                sections.append(current_section.strip())
            current_section = line
        else:
            if len(current_section) + len(line) + 1 > max_size:
                # This section would be too big
                return []
            if current_section:
                current_section += '\n' + line
            else:
                current_section = line
    
    if current_section:
        sections.append(current_section.strip())
    
    return sections if all(len(s) <= max_size for s in sections) else []


def format_ban_results(ban_event_map: Dict[str, List[str]]) -> str:
    """Format ban results for display.
    
    Args:
        ban_event_map: Dictionary containing ban results
        
    Returns:
        str: Formatted ban results
    """
    ban_list = ban_event_map.get("ban_list", {})
    error_list = ban_event_map.get("error_list", {})
    
    result_parts = []
    
    for user, rooms in ban_list.items():
        if rooms:
            result_parts.append(f"Banned {user} from: {', '.join(rooms)}")
    
    for user, rooms in error_list.items():
        if rooms:
            result_parts.append(f"Failed to ban {user} from: {', '.join(rooms)}")
    
    return '\n'.join(result_parts) if result_parts else "No ban operations performed"


def format_sync_results(sync_results: Dict[str, List[str]]) -> str:
    """Format sync results for display.
    
    Args:
        sync_results: Dictionary containing sync results
        
    Returns:
        str: Formatted sync results
    """
    added = sync_results.get("added", [])
    dropped = sync_results.get("dropped", [])
    
    added_str = "<br />".join(added) if added else "none"
    dropped_str = "<br />".join(dropped) if dropped else "none"
    
    return f"Added: {added_str}<br /><br />Dropped: {dropped_str}"
