/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Host test of the real backend and upstream codec; only kernel APIs are stubbed. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <linux/lz4kd.h>
#include "backend_lz4kd.h"

int fail_allocation;

static unsigned char input[4096], compressed[8192], output[4096];
static unsigned int random_state = 0x12345678;

static unsigned char random_byte(void)
{
	random_state ^= random_state << 13;
	random_state ^= random_state >> 17;
	random_state ^= random_state << 5;
	return random_state;
}

int main(void)
{
	struct zcomp_params params = { .level = ZCOMP_PARAM_NO_LEVEL };
	struct zcomp_ctx ctx = {};
	int raw_pages = 0, compressed_pages = 0;

	assert(!backend_lz4kd.setup_params(&params));
	params.dict_sz = 1;
	assert(backend_lz4kd.setup_params(&params) == -EINVAL);
	params.dict_sz = 0;
	params.level = 1;
	assert(backend_lz4kd.setup_params(&params) == -EINVAL);
	params.level = ZCOMP_PARAM_NO_LEVEL;
	fail_allocation = 1;
	assert(backend_lz4kd.create_ctx(&params, &ctx) == -ENOMEM);
	assert(!ctx.context);
	fail_allocation = 0;
	assert(!backend_lz4kd.create_ctx(&params, &ctx));

	for (int page = 0; page < 1000; page++) {
		struct zcomp_req encode = {
			.src = input, .src_len = sizeof(input),
			.dst = compressed, .dst_len = sizeof(compressed),
		};
		for (size_t i = 0; i < sizeof(input); i++) {
			if (page % 4 == 0)
				input[i] = 0;
			else if (page % 4 == 1)
				input[i] = i % 37;
			else if (page % 4 == 2)
				input[i] = i < 3000 ? 0 : random_byte();
			else
				input[i] = random_byte();
		}
		memset(compressed, 0xa5, sizeof(compressed));
		assert(!backend_lz4kd.compress(&params, &ctx, &encode));
		assert(encode.dst_len > 0 && encode.dst_len <= sizeof(compressed));
		if (encode.dst_len >= sizeof(input)) {
			/* Same fallback as zram_write_page: store the original page. */
			raw_pages++;
			memcpy(output, input, sizeof(input));
		} else {
			struct zcomp_req decode = {
				.src = compressed, .src_len = encode.dst_len,
				.dst = output, .dst_len = sizeof(output),
			};
			compressed_pages++;
			memset(output, 0xff, sizeof(output));
			assert(!backend_lz4kd.decompress(&params, &ctx, &decode));
		}
		assert(!memcmp(input, output, sizeof(input)));
	}
	assert(raw_pages > 0 && compressed_pages > 0);

	/* A positive but short decode is not a successful full-page read. */
	memset(input, 'x', sizeof(input));
	int len = lz4kd_encode(ctx.context, input, compressed, 2048,
			       sizeof(compressed), 0);
	assert(len > 0);
	struct zcomp_req short_page = {
		.src = compressed, .src_len = len,
		.dst = output, .dst_len = sizeof(output),
	};
	assert(backend_lz4kd.decompress(&params, &ctx, &short_page) == -EINVAL);

	struct zcomp_req truncated = {
		.src = compressed, .src_len = 1,
		.dst = output, .dst_len = sizeof(output),
	};
	assert(backend_lz4kd.decompress(&params, &ctx, &truncated) == -EINVAL);
	struct zcomp_req large_page = {
		.src = input, .src_len = 16384,
		.dst = compressed, .dst_len = sizeof(compressed),
	};
	assert(backend_lz4kd.compress(&params, &ctx, &large_page) == -EINVAL);
	struct zcomp_req small_buffer = {
		.src = input, .src_len = sizeof(input),
		.dst = compressed, .dst_len = 1,
	};
	assert(backend_lz4kd.compress(&params, &ctx, &small_buffer) == -EINVAL);
	backend_lz4kd.destroy_ctx(&ctx);
	assert(!ctx.context);
	backend_lz4kd.release_params(&params);
	printf("PASS: %d compressed pages, %d raw pages; errors and context lifecycle\n",
	       compressed_pages, raw_pages);
	return 0;
}
