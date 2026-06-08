"""MCP 工具定义与描述"""
from typing import Dict, List


TOOL_DEFINITIONS: Dict[str, Dict] = {
    "sys_info": {
        "description": "获取系统信息（CPU、内存、磁盘、负载等）",
        "parameters": {
            "type": "object",
            "properties": {
                "metric": {
                    "type": "string",
                    "enum": ["cpu", "memory", "disk", "load", "all"],
                    "description": "要查询的指标类型",
                }
            },
            "required": ["metric"],
        },
    },
    "service_mgr": {
        "description": "管理系统服务（systemctl 操作）",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["start", "stop", "restart", "status", "enable", "disable"],
                    "description": "操作类型",
                },
                "service": {
                    "type": "string",
                    "description": "服务名称，例如 nginx, sshd",
                },
            },
            "required": ["action", "service"],
        },
    },
    "log_reader": {
        "description": "读取系统日志",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "日志来源，如 /var/log/messages 或 journalctl",
                },
                "lines": {
                    "type": "integer",
                    "default": 50,
                    "description": "读取行数",
                },
            },
            "required": ["source"],
        },
    },
    "net_monitor": {
        "description": "网络监控信息",
        "parameters": {
            "type": "object",
            "properties": {
                "iface": {
                    "type": "string",
                    "default": "all",
                    "description": "网卡接口名",
                },
            },
        },
    },
    "cmd_exec": {
        "description": "执行安全范围内的系统命令",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的命令",
                },
                "timeout": {
                    "type": "integer",
                    "default": 30,
                    "description": "超时秒数",
                },
            },
            "required": ["command"],
        },
    },
    "file_guard": {
        "description": "安全地读取文件内容",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件路径",
                },
                "max_size": {
                    "type": "integer",
                    "default": 65536,
                    "description": "最大读取字节数",
                },
            },
            "required": ["path"],
        },
    },
}


def get_tool_names() -> List[str]:
    """获取所有工具名称"""
    return list(TOOL_DEFINITIONS.keys())


def get_tool_schema(tool_name: str) -> Dict:
    """获取指定工具的 JSON Schema"""
    return TOOL_DEFINITIONS.get(tool_name, {})
