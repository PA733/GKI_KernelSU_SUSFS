ui_print "Selects LZ4KD only when the matching ZRAM module is available."
ui_print "The paired boot and vendor_kernel_boot images are required for LZ4KD."
set_perm "$MODPATH/post-fs-data.sh" 0 0 0755
