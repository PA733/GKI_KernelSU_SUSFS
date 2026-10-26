ui_print "Selects LZ4KD only when the matching ZRAM module is available."
ui_print "Install the paired boot and vendor_kernel_boot images first."
set_perm "$MODPATH/post-fs-data.sh" 0 0 0755
