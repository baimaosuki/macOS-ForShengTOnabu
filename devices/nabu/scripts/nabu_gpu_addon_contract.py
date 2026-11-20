"""Installation contracts of the signed macOS GPU bundle and Shader Cache app."""
import plistlib

SHADER_CACHE_APP = '/Applications/Shader Cache.app'
REGISTER_JOB = 'Library/LaunchAgents/org.unixnu.ShaderCache.register.plist'
METAL_PUBLISH_MARKER = 'GPUBundles/NativeAdrenoMetal.publish'
METAL_PUBLISH_CONTENT = (0o644, b'1\n')


def metal_publication_contract(entries):
    # NativeAdrenoMetalDevice checks this sibling with access(R_OK) before
    # deviceForPort(). Without it initWithAcceleratorPort returns nil and runs
    # the standalone diagnostic instead. gpu-metal=1 publishes the kernel
    # accelerator; it does not select the plugin's production path.
    if entries.get(METAL_PUBLISH_MARKER) != METAL_PUBLISH_CONTENT:
        raise ValueError('Metal production addon lacks its readable publication marker')
    root = 'GPUBundles/NativeAdrenoMetal.bundle/Contents/'
    for relative in ('MacOS/NativeAdrenoMetal', 'MacOS/libvulkan_freedreno.dylib'):
        mode, data = entries[root + relative]
        if mode != 0o755 or not data:
            raise ValueError('Metal production executable is missing or has an incorrect mode')
    return dict(publication_enabled=True,
                marker='/Library/' + METAL_PUBLISH_MARKER,
                marker_mode='0644', marker_bytes=2,
                plugin_path='deviceForPort', diagnostic_only=False)


def shader_cache_contract(entries):
    root = SHADER_CACHE_APP.lstrip('/') + '/'
    argument = SHADER_CACHE_APP + '/Contents/MacOS/ShaderCacheRegister'
    mode, data = entries[REGISTER_JOB]
    job = plistlib.loads(data)
    if mode != 0o644 or job.get('ProgramArguments') != [argument]:
        raise ValueError('Shader Cache registration job does not name the installed signed tool')
    for executable in ('ShaderCache', 'ShaderCacheRegister', 'ShaderCacheWorker'):
        mode, data = entries[root + 'Contents/MacOS/' + executable]
        if mode != 0o755 or not data:
            raise ValueError('Shader Cache executable is absent or has an incorrect mode')
    register = entries[root + 'Contents/MacOS/ShaderCacheRegister'][1]
    if SHADER_CACHE_APP.encode() + b'\0' not in register:
        raise ValueError('Signed registration binary embeds a different application path')
    return dict(application=SHADER_CACHE_APP, registration_program=argument,
                signed_registration_path_verified=True, executable_bytes_unchanged=True)
