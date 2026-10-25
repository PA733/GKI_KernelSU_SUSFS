// SPDX-License-Identifier: GPL-2.0-or-later
#include <linux/kernel.h>
#include <linux/lz4kd.h>
#include <linux/sizes.h>
#include <linux/vmalloc.h>

#include "backend_lz4kd.h"

static void lz4kd_release_params(struct zcomp_params *params)
{
}

static int lz4kd_setup_params(struct zcomp_params *params)
{
	/* This backend uses independent blocks, without a dictionary or level. */
	if (params->dict_sz || params->level != ZCOMP_PARAM_NO_LEVEL)
		return -EINVAL;
	return 0;
}

static int lz4kd_create(struct zcomp_params *params, struct zcomp_ctx *ctx)
{
	ctx->context = vmalloc(lz4kd_encode_state_bytes_min());
	return ctx->context ? 0 : -ENOMEM;
}

static void lz4kd_destroy(struct zcomp_ctx *ctx)
{
	vfree(ctx->context);
	ctx->context = NULL;
}

static int lz4kd_compress(struct zcomp_params *params, struct zcomp_ctx *ctx,
			  struct zcomp_req *req)
{
	int ret;

	/* The upstream encoder accepts at most one 4 KiB page. */
	if (req->src_len != SZ_4K || req->dst_len < SZ_4K)
		return -EINVAL;
	ret = lz4kd_encode(ctx->context, req->src, req->dst,
			  req->src_len, req->dst_len, 0);
	if (ret < 0)
		return -EINVAL;

	/*
	 * A zero result has no usable compressed data. ZRAM stores the original
	 * page when the reported size is PAGE_SIZE, including on recompression.
	 */
	req->dst_len = ret ? ret : req->src_len;
	return 0;
}

static int lz4kd_decompress(struct zcomp_params *params, struct zcomp_ctx *ctx,
			    struct zcomp_req *req)
{
	int ret;

	if (req->dst_len != SZ_4K)
		return -EINVAL;
	ret = lz4kd_decode(req->src, req->dst, req->src_len, req->dst_len);
	/* A successful ZRAM read must restore the entire page. */
	return ret == (int)req->dst_len ? 0 : -EINVAL;
}

const struct zcomp_ops backend_lz4kd = {
	.compress	= lz4kd_compress,
	.decompress	= lz4kd_decompress,
	.create_ctx	= lz4kd_create,
	.destroy_ctx	= lz4kd_destroy,
	.setup_params	= lz4kd_setup_params,
	.release_params	= lz4kd_release_params,
	.name		= "lz4kd",
};
