/* SPDX-License-Identifier: MIT */
extern void native_adreno_a6xx_start(void);
void nabu_gpu_start_after_iobsd(void);
/* Called only by the board's late display hook after genuine IOBSD publication.
 * GPU workers are ordinary BSD/IOKit clients, not static startup initializers. */
void nabu_gpu_start_after_iobsd(void)
{
    native_adreno_a6xx_start();
}
