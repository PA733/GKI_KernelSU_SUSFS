# Pixel A9 LZ4KD 配套镜像

Pixel 的 ZRAM 位于 `vendor_kernel_boot`，由 `zram_gs.ko` 和 `zcomp_gs_cpu.ko` 实现。通用 GKI 的 ZRAM 配置不会给这两个原厂模块增加算法。将 ZSMALLOC 改为内建还会与原厂 `zsmalloc.ko` 重复导出符号，导致第一阶段启动失败。

修复构建保留 ZRAM/ZSMALLOC 的模块形式，并输出同一次编译的 `Image`、签名 `zram.ko` 和 `vmlinux.symvers`。配套工具将原厂 ramdisk 中的两个 ZRAM 模块替换成新的 `zram.ko`，保留原厂 `zsmalloc.ko`、DTB、其他驱动及镜像元数据。它会验证内核版本、模块依赖与导入符号 CRC，并使用 Image 中嵌入的证书验证模块签名；遇到其他驱动依赖原厂 ZRAM 时拒绝修改。

## 准备

适用于本仓库 Pixel A9 固定生产快照、4 KiB 页、已解锁的设备。先保存当前系统版本的完整 `boot` 和 `vendor_kernel_boot` 分区备份，确认当前 slot。不要混用其他系统版本的镜像，也不要覆盖原始备份。

触发 Pixel 工作流，设置 `clean_build=false`、`use_zram=true`。下载名称以 `-pixel-zram` 结尾的配套产物，解压为一个目录。在安装了 Python 3、`lz4` 和 `openssl` 命令的 Linux 主机上运行：

```sh
python3 prepare_pixel_zram.py \
  --bundle ./pixel-zram-bundle \
  --boot ./boot-stock.img \
  --vendor-kernel-boot ./vendor_kernel_boot-stock.img \
  --output ./pixel-lz4kd-images
```

工具只生成本地文件，不会连接或刷写设备。它支持 Android boot v4 和单 ramdisk fragment 的 Pixel vendor_boot v4，保留原有 kernel 压缩方式。输出不保留已失效的旧 AVB/GKI 签名，仅用于已解锁设备。

输出目录包含 `boot-lz4kd.img`、`vendor_kernel_boot-lz4kd.img`、原始镜像副本及 SHA-256 清单。`--boot` 可省略，此时仅生成配套 `vendor_kernel_boot`，内核需通过同一次 CI 构建的 AnyKernel3 包安装。

## 安装与验证

将两个匹配的镜像安装到已确认的同一 slot。必须配对更新：新 `zram.ko` 的签名与符号依赖来自该次编译的内核。不要只更新 `vendor_kernel_boot`。

可提前在 ReSukiSU 中安装 `pixel-lz4kd-config.zip`，使配对镜像第一次启动时就选择 LZ4KD。配置模块在 `post-fs-data` 阶段检测 `lz4kd` 是否可用，仅在 ZRAM 尚未初始化时设置 `mmd.zram.comp_algorithm=lz4kd`。它不会对已启用的 swap 执行 reset 或 swapoff；回退到不含 LZ4KD 的原厂模块时不修改属性。

重启后检查：

```sh
getprop sys.boot_completed
getprop mmd.setup_complete
getprop mmd.zram.comp_algorithm
cat /sys/block/zram0/comp_algorithm
cat /proc/swaps
cat /data/adb/modules/pixel_lz4kd_config/last-status.txt
```

应看到系统启动完成、mmd 初始化成功、`[lz4kd]` 及已启用的 zram swap。若算法未选中，先检查配置模块状态和系统属性；不要直接重置正在使用的 swap。

## 回退

向原 slot 恢复保存的 `boot-stock.img` 和 `vendor_kernel_boot-stock.img`，并禁用或卸载算法配置模块。系统 OTA 后需基于新版本的原始镜像重新适配，不能复用旧 `vendor_kernel_boot`。
