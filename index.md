---
title: '用 pyOCD 与 Cortex-Debug 在挂机异常后连接 MCU 查看现场'
coverText: 'pyOCD配合Cortex-Debug在挂机异常后查看MCU现场'
date: '2026-07-28'
author: 'createskyblue'
email: 'createskyblue@outlook.com'
tags: ['嵌入式', '断点调试', '故障诊断', 'VSCode', 'pyocd', 'gdb', 'Cortex', 'halt', 'attach']
coverImage: ''
---

# 用 pyOCD 与 Cortex-Debug 在挂机异常后连接 MCU 查看现场

几十台产品需要长期挂机，异常可能数小时或数天后出现。每台一直挂调试器不现实，启动调试还可能复位目标。设备平时独立运行，出现停转、卡死或状态异常后，再通过 SWD 暂停 MCU 查看现场。

![Vscode_Cortex_Debug断点调试](./PixPin_2026-07-28_11-56-24.jpg)

## Reset 与现场边界

常规 Launch 调试会让程序从复位入口重新开始：

```text
Reset -> Reset_Handler -> SystemInit -> main
```

现场连接需要保留当前执行位置：

```text
异常位置 -> SWD 连接 -> Debug Halt -> 读取 CPU 状态
```

一旦执行 Reset，`PC`、`SP`、调用栈和外设状态都会发生变化，故障现场也随之改变。

目标中的固件与本地 ELF 必须来自同一次构建。`executable` 只用于加载 ELF 符号，把地址映射到函数、源码和变量；现场调试不要执行 GDB 的 `load` 命令。`load` 会进入 Flash 编程流程，可能执行擦除、写入或复位，直接改变现场。ELF 与设备固件不一致时，调用栈和变量显示可能失真。

这套方法保存的是“调试器暂停 MCU 那一刻”的状态。若看门狗已经复位设备，或程序继续运行并覆盖了故障栈，原始现场无法靠延迟连接恢复。

## Cortex-Debug Attach 配置

Cortex-Debug 应使用 `request: "attach"`。它跳过 Launch 模式默认的下载和启动流程，不会主动把设备重新运行到 `main`。自定义 GDB 命令、启动脚本和 GDB Server 仍可能触发 Reset，使用前需要逐项检查。

```json
{
  "name": "MCU Attach (pyOCD)",
  "type": "cortex-debug",
  "request": "attach",
  "cwd": "${workspaceFolder}",
  "servertype": "pyocd",
  "targetId": "TARGET_ID",
  "executable": "./build/firmware.elf",
  "cmsisPack": "./pack/Vendor.Device.pack",
  "svdFile": "./svd/device.svd",
  "serverArgs": [
    "-O",
    "connect_mode=attach"
  ],
  "overrideGDBServerStartedRegex": "GDB server listening on port"
}
```

当前版本的 Cortex-Debug 无法识别新版 pyOCD 的启动输出，需要用 `overrideGDBServerStartedRegex` 指定成功日志。否则 pyOCD 已监听端口，Cortex-Debug 仍可能停在等待启动。

异常出现后，在 VS Code 中启动这项配置。`connect_mode=attach` 只控制 pyOCD 初始化阶段，此时 pyOCD 不主动暂停 CPU。

本机 pyOCD 0.44.1 会在 GDB 接入时调用 `halt()`，所以此版本冻结点位于 GDB 连接阶段；其他版本请以启动日志为准。

## Attach 与 Halt 的冻结时机

状态仍在快速变化时，等待 GDB 完成连接可能太晚。把连接模式改成 `halt`，pyOCD 会在 GDB Server 初始化期间暂停内核。

```json
"serverArgs": [
  "-O",
  "connect_mode=halt"
],
"overrideGDBServerStartedRegex": "GDB server listening on port"
```

暂停由 Cortex-M 调试硬件完成，CPU 无需运行到某个暂停函数。概念链路如下：

```text
SWD -> DAP -> DHCSR.C_HALT -> CPU 停止
```

两种模式都配合 Cortex-Debug 的 `request: "attach"` 使用：

| pyOCD 模式 | 暂停时机 | 使用场景 |
| --- | --- | --- |
| `attach` | GDB 客户端接入时 | 状态稳定 |
| `halt` | pyOCD 连接目标时 | 状态变化快 |

## 独立 GDB Server 提前冻结

需要先冻结、后打开 VS Code 时，可以运行一次性脚本。它用 `halt` 启动 pyOCD GDB Server，冻结 MCU 后等待 GDB 接入。

```bat
@echo off
pyocd gdbserver --pack .\pack\Vendor.Device.pack -t TARGET_ID -O connect_mode=halt
pause
```

VS Code 使用外部 GDB Server，并继续采用 `request: "attach"`：

```json
{
  "name": "MCU External Attach",
  "type": "cortex-debug",
  "request": "attach",
  "servertype": "external",
  "gdbTarget": "localhost:3333",
  "executable": "./build/firmware.elf",
  "svdFile": "./svd/device.svd"
}
```

脚本只启动一次。参数错误或 GDB Server 退出后，`pause` 会保留错误信息，避免快速重复启动 pyOCD。

几十台设备可以继续独立挂机，调试探针只接到发生异常的设备上。`halt` 负责尽早冻结，Cortex-Debug 负责把寄存器、源码和变量组织成可读现场。
