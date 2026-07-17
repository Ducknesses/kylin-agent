# cgroups 沙箱资源限制 技术债记录

## 1. 问题描述

MCP Server 的 sandbox.py 使用 subprocess.run(timeout=30) 执行命令，缺少操作系统级资源隔离：
- timeout 只能限制执行时长，不能限制 CPU/内存/IO 消耗
- 子进程逃逸（timeout 只杀父进程）
- 无 fork bomb 防护
- 多任务争抢宿主机资源

## 2. 原因

- 项目初期快速迭代，安全焦点在命令白名单和权限控制
- cgroups 需要系统级配置（root 或 systemd Delegate），增加了部署复杂度
- 缺少 cgroups v2 委派环境

## 3. 当前执行模型（修改前）

```
sandbox.execute() → 白名单校验 → 危险字符过滤 → 路径保护 → subprocess.run(..., timeout=30, shell=False)
```

问题：无进程组机制、无资源限制。

## 4. cgroups v1/v2 选择

选择 cgroups v2，理由：
- 当前环境已是 v2（cgroup2fs，controllers: cpuset cpu io memory hugetlb pids rdma）
- v1 已被内核标记为 deprecated
- v2 接口更简洁统一

## 5. 实现架构

```
sandbox.execute()
  ├─ 现有安全校验（不变）
  ├─ CgroupV2Limiter.setup(task_id)         ← 新增
  │    ├─ 检测 cgroups v2 支持
  │    ├─ 创建 /sys/fs/cgroup/mcp-server/task-{uuid}
  │    └─ 写入 cpu.max / memory.max / memory.swap.max / pids.max / io.max
  ├─ subprocess.Popen(cmd, start_new_session=True, shell=False)  ← 改造
  ├─ CgroupV2Limiter.attach(pid)            ← 新增
  ├─ proc.communicate(timeout=...)           ← 改造
  └─ finally: limiter.cleanup()              ← 新增（kill_all + rmdir）
```

## 6. CPU 限制

- 使用 `cpu.max` 控制器
- 配置：CGROUP_CPU_QUOTA=50000（微秒/100ms），CGROUP_CPU_PERIOD=100000（微秒）
- 含义：每 100ms 周期内最多使用 50ms CPU（50% 单核）
- 写入内容：`"50000 100000"` → /sys/fs/cgroup/mcp-server/task-{id}/cpu.max

## 7. Memory 限制

- 使用 `memory.max` 控制器
- 配置：CGROUP_MEMORY_MAX=268435456（256MB）
- 超限行为：内核 OOM killer 终止 cgroup 内进程
- 写入内容：`"268435456"` → memory.max

## 8. IO 限制

- 使用 `io.max` 控制器（**默认未启用**）
- 配置：CGROUP_IO_MAX=""（空字符串 = IO 限制禁用）
- 原因：需要设备 major:minor 映射，不能写死特定机器值
- 格式校验：MAJOR:MINOR KEY=VALUE（rbps/wbps/riops/wiops），拒绝非法设备号、路径注入、多行注入
- 如需启用：填入 "8:0 rbps=10485760 wiops=100" 格式
- **当前状态：IO 限制未启用，真实设备号需要部署环境确认**

## 9. PIDs 限制

- 使用 `pids.max` 控制器（已实现）
- 配置：CGROUP_PIDS_MAX=64
- 原因：防止 fork bomb 或异常多进程，是 cgroups 沙箱的必要安全补充
- 写入内容：`"64"` → pids.max

## 10. 进程启动竞态

**当前状态**：Popen → attach(pid) 模型存在短暂竞态窗口。

Python Popen 启动进程后，将 PID 写入 cgroup.procs 之间存在逃逸窗口：
- 进程在加入 cgroup 前可短暂无限制运行
- 若进程在 attach 前 fork 子进程，子进程不受 cgroup 限制

**缓解措施**：
1. attach 失败立即终止完整进程组（killpg）
2. attach 完成前不调用 communicate
3. 使用 start_new_session=True 创建独立进程组，退出时清理完整进程树

**未解决**：Python 3.10 环境下无法彻底消除 Popen→attach 竞态窗口。
后续升级 Python 3.11+ 可考虑 pidfd_open 或 clone3 方案。

**重要**：当前实现不构成"原子资源限制"，描述为"任务级 cgroup 附加 + 进程组终止"。

## 11. timeout 和进程树处理

- 使用 `start_new_session=True` 创建新进程组
- timeout 后：SIGTERM → 200ms → SIGKILL 完整进程组（os.killpg）
- cleanup 时：读取 cgroup.procs，逐 PID SIGKILL
- 确保不残留子/孙进程

## 12. fail-closed

- CGROUP_ENABLED=true 但环境不支持 → ResourceLimitError → 返回 blocked=True
- cgroup 创建失败 → 不继续执行命令
- 写入限制失败 → 清理已创建目录，返回 blocked
- 禁止静默 logger.warning 后继续无限制执行

## 13. 测试环境限制

- 单元测试：使用 tempfile.TemporaryDirectory + monkeypatch 模拟 cgroup 文件系统（已全部通过）
- 真实内核集成测试：需要 root 启用 subtree_control 或 systemd Delegate=yes
- 当前环境：subtree_control 为空，无法执行真实集成测试
- 后续在配置 Delegate=cpu memory io pids 的麒麟 V11 环境执行

## 14. 真实集成测试状态

**未执行。** 原因：当前环境 subtree_control 为空，agent-read 用户无 cgroup 写权限。

需要在以下环境执行：
```bash
# 1. 启用委派（root）
echo "+cpu +memory +io +pids" > /sys/fs/cgroup/cgroup.subtree_control

# 2. 或 systemd 配置（mcp-server.service）
Delegate=cpu memory io pids

# 3. 运行集成测试
pytest mcp-server/tests/test_resource_limiter.py -m integration -v
```

## 15. 未处理事项

- systemd Delegate 自动配置（部署脚本，不在本轮范围）
- **其他 MCP 插件存在独立 subprocess 路径（service_mgr/log_reader/sys_info/net_monitor/mcp_self_monitor）**
- **其他插件的统一资源限制属于后续独立任务，本分支不修改**
- LoongArch 验证（第 9 项，不在此分支）
- io.max 设备映射自动检测
- 真实 cgroups v2 内核集成测试尚未执行（环境无 subtree_control 委派）

## 16. 风险

| 风险 | 缓解 |
|-|-|
| 无 cgroup 写权限 | Delegate=yes 或 root 委派；否则 CGROUP_ENABLED=false |
| subtree_control 空 | fail-closed，必须显式配置委派 |
| 进程启动竞态 | preexec_fn 方案，记录技术债 |
| 限制过低 | 默认值保守（256MB 内存、50% CPU） |
| OOM 后残留 cgroup | cleanup 在 finally 中执行 |

## 17. 回滚方案

1. 设置 CGROUP_ENABLED=false → 退回仅 timeout 模式（日志记录）
2. git revert 本次提交
3. 清理残留 cgroup: `rmdir /sys/fs/cgroup/mcp-server/task-*`

## 18. 验收结果

- [x] 单元测试（ResourceLimiter + sandbox 集成）全部通过
- [x] 白名单/危险字符/路径保护/shell=False 不变
- [x] API 协议不变
- [x] blocked 语义不变
- [x] cleanup 使用 os.rmdir（非 shutil.rmtree）
- [x] IO 格式校验（拒绝路径注入/多行/非法设备号）
- [x] attach 失败立即 killpg + fail-closed
- [x] 不声称"原子资源限制"
- [x] 技术债明确 Popen→attach 竞态
- [ ] 真实内核集成测试未执行（环境限制）
