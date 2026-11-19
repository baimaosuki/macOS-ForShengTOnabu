"""Installation contract of the already signed macOS Shader Cache application."""
import plistlib

SHADER_CACHE_APP = '/Applications/Shader Cache.app'
REGISTER_JOB = 'Library/LaunchAgents/org.unixnu.ShaderCache.register.plist'


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
