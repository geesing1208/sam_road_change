# 数据包接入验收记录（2026-10-29）

本轮验证接入后的可靠性，不改变 Plugin API、CLI 参数、正式成果目录或模型算法。原 TXT、单幅影像和目录输入继续使用同一后端流水线。

## 数据与环境

- Windows；插件宿主 `gui_env/python.exe`，GIS/推理环境 `runtime/env/samroad_env/python.exe`；RTX 4060 Laptop，8 GB 显存。
- 原始数据只读，独立工程保存在 `_work/production_acceptance_20261029/`，不纳入 Git。
- `给万一/给万一`：2021—2024 四幅原始影像，EPSG:4526；2021 年 2 m，后续 1 m。另有按年份与层级组织的 EPSG:4490 瓦片及索引 SHP。
- `ToWHU/ToWHU`：按完整日期/卫星产品/层级组织的 EPSG:4490 RGB 瓦片；四期分别 250、250、250、249 张。最后一期缺少一张瓦片。索引 SHP 不作道路真值，影像信息 XLSX 仅提供说明，不替代影像坐标信息。

## 已发现并修复的问题

1. 非法或多日期字符串不再退化识别成年份；同一期不同切图产品禁止合并。
2. 共同有效范围采用平衡合并、缓存坐标转换，并在读取掩膜前检查原生块大小；保持逐像元掩膜与孔洞，不降低空间精度。
3. 生成范围使用临时文件和完成标记校验；中断或组件损坏后重新生成。
4. 大量瓦片的预检查曾超过 180 秒：移除循环内反复解析同一期 TXT，并复用坐标转换检查。修复后两期 500 张瓦片检查为数据包 34.94 秒、TXT 41.81 秒（单次测量）。
5. 数据包扫描/范围生成可取消，取消后回收子进程；取消或失败不提交界面配置。移除固定 600 秒超时。
6. 真实推理暴露 Windows 长路径问题：缩短 IR-MAD 临时目录名但保持完整缓存身份校验；提取输出与续跑标记遍历使用 Windows 扩展 I/O 路径。迁移比较兼容普通路径和扩展路径。

## 真实验证结果

### 数据包与原 TXT 同源对照

`towhu_small/` 使用 20260214、20260325 两期、每期 250 张源瓦片；共同范围内约 1.048576 km² 作业区，两工程具有相同范围、参数、参考期和原始影像。通过实际插件 Controller/QProcess 启动，运行真实模型。

- 数据包工程首次执行及后续续跑暴露了上述长路径问题；保留失败日志后沿原断点继续，最终完成两期道路提取、测宽、一对变化和长时序，失败数 0。
- TXT 工程独立完整运行成功，失败数 0，后端报告 168.468 秒。
- `comparison.json`：2 份规范化栅格的 CRS、网格、尺寸、像元和有效掩膜哈希完全一致；10 份正式 SHP 的字段、全部属性与标准化几何哈希完全一致。
- SHP 组件完整、几何有效，没有空或缺失几何；空变化图层不作为失败。
- 测宽存在低可信/估计宽度提示，不等于测量精度已经通过验证。

### 全瓦片范围计算

`coverage_towhu/benchmark.json`：四期共 999 张瓦片，基线 `d307a9f` 用时 54.063 秒，本轮 9.390 秒；共同覆盖的对称差面积为 0 m²。单次同机计时，约 5.76 倍加速，不作为通用性能承诺。

### 重新打开与迁移

将已完成的数据包工程完整复制到 `towhu_small/relocated/`，通过 `Controller.prepare_project_paths` 更新工程路径并重新扫描：状态 completed，2 期、11 项可读取成果；10 份正式 SHP 与原工程逐字节一致。再通过实际 Controller 执行长时序重建，成功完成，日志为 `temporal_rebuild.log`。原目录仍存在，用于验证复制后不会继续指向旧成果。此项是同盘目录迁移；跨盘与网络盘尚未实测。

### 较大范围

`annual_full/` 使用四期整幅影像的完整共同范围 11.037846 km²，数据包与 TXT 工程均通过预检查。数据包工程实际完整运行成功：4 期、3 对变化、长时序汇总，失败数 0。16 份正式 SHP 组件完整、CRS 存在、几何有效，没有空或缺失几何；检查结果保存在 `package/formal_audit.json`。该范围的 TXT 工程仅执行预检查，未另跑模型；入口等价性由上述两期同源对照验证。

`package/resources.json`：总墙钟时间 429.063 秒；约每 0.5 秒采样，802 次记录的进程树峰值工作集 2,917,335,040 字节（约 2.72 GiB），不是显存峰值；工程文件共 518,261,712 字节（约 494.25 MiB）。后端记录模型加载一次、复用三次，没有 OOM 或异常退出。这是单次本机结果，不能外推任意数据规模。

## 自动检查

本轮通过：Python 语法解析；`git diff --check`；以下专项测试共 120 项：

| 测试 | 数量 | 环境 |
| --- | ---: | --- |
| test_package_process | 4 | GUI |
| test_workbench | 18 | GUI |
| test_image_import | 17 | GUI |
| test_plugin_project_relocation | 6 | GUI |
| test_project_state | 27 | GUI |
| test_plugin | 16 | GUI |
| test_input_packages | 13 | GIS |
| test_cache_commit | 8 | GIS |
| check_irmad_cache_recovery | 11 | GIS |

后端快照仅更新本轮修改的五个后端文件。

## 复现入口与限制

`tests/production_acceptance.py` 是显式运行脚本，不由 unittest 自动发现。`prepare` 要求新目录并生成隔离工程；`configure` 拒绝覆盖已执行工程；`run` 使用真实插件驱动；`audit` 比较两个工程，差异会返回失败；`coverage_benchmark` 对照基线范围实现。

```powershell
& runtime/env/samroad_env/python.exe tests/production_acceptance.py run <验收目录>/package
& runtime/env/samroad_env/python.exe tests/production_acceptance.py run <验收目录>/txt
& runtime/env/samroad_env/python.exe tests/production_acceptance.py audit <验收目录>
```

本次数据包没有道路真值，未进行精度评价或人工目视精度验收；自动参考期推荐、跨盘迁移与整个 ToWHU 四期约 85.61 km² 的模型推理未验证。小范围首次资源统计因验收脚本普通路径遍历失败而未保存，不能报告其成功运行的峰值内存；脚本已修正为扩展路径。失败日志、最终成功状态和成果仍完整保留。
