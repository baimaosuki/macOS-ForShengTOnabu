/* SPDX-License-Identifier: MIT */
#ifndef NABU_STATIC_DESKTOP_H
#define NABU_STATIC_DESKTOP_H
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#ifndef NABU_DESKTOP_DATA_ROOT
#define NABU_DESKTOP_DATA_ROOT "/System/Volumes/Data"
#endif
static int nabu_desktop_write(const char *path, const void *data, size_t bytes) {
    char temporary[1024];
    if (snprintf(temporary,sizeof temporary,"%s.new",path) >= (int)sizeof temporary) return -1;
    int fd=open(temporary,O_WRONLY|O_CREAT|O_TRUNC|O_NOFOLLOW,0644);
    if(fd<0)return -1;
    const char *p=data; size_t left=bytes;
    while(left){ssize_t n=write(fd,p,left);if(n<0 && errno==EINTR)continue;if(n<=0){close(fd);unlink(temporary);return -1;}p+=n;left-=(size_t)n;}
    int status=fsync(fd);if(close(fd))status=-1;
    if(status || rename(temporary,path)){unlink(temporary);return -1;}
    return 0;
}
static int nabu_install_static_desktop(void) {
    static const char *dirs[]={"/Library", "/Library/Application Support", "/Library/Application Support/NABU", "/Library/Application Support/NABU/Desktop", "/Library/LaunchAgents"};
    char path[1024];
    for(unsigned i=0;i<sizeof dirs/sizeof dirs[0];++i){
        snprintf(path,sizeof path,"%s%s",NABU_DESKTOP_DATA_ROOT,dirs[i]);
        if(mkdir(path,0755) && errno!=EEXIST)return -1;
    }
    static const unsigned char image[]={0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52, 0x00, 0x00, 0x00, 0x10, 0x00, 0x00, 0x00, 0x10, 0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x91, 0x68, 0x36, 0x00, 0x00, 0x00, 0x16, 0x49, 0x44, 0x41, 0x54, 0x78, 0xda, 0x63, 0x90, 0xb5, 0x0b, 0x23, 0x09, 0x31, 0x8c, 0x6a, 0x18, 0xd5, 0x30, 0x7c, 0x35, 0x00, 0x00, 0xe1, 0xe2, 0xb1, 0x01, 0xeb, 0x67, 0x5b, 0x4c, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4e, 0x44, 0xae, 0x42, 0x60, 0x82};
    static const char script[]="// Owner static desktop: use AppKit directly, no AppleEvents or Metal wallpaper.\nObjC.import('AppKit');\nObjC.import('Foundation');\nfunction run(argv) {\n    var fm = $.NSFileManager.defaultManager;\n    var folder = ObjC.unwrap($.NSHomeDirectory()) + '/Library/Application Support/NABU';\n    var marker = folder + '/static-desktop-v1';\n    if (fm.fileExistsAtPath($(marker))) return 'NABU static desktop already configured';\n    var url = $.NSURL.fileURLWithPath($('/System/Volumes/Data/Library/Application Support/NABU/Desktop/desktop.png'));\n    var screens = $.NSScreen.screens;\n    if (!screens.count) throw new Error('Aqua display is not ready');\n    var ws = $.NSWorkspace.sharedWorkspace;\n    for (var i = 0; i < screens.count; ++i) {\n        var error = Ref();\n        if (!ws.setDesktopImageURLForScreenOptionsError(url, screens.objectAtIndex(i), $({}), error))\n            throw new Error('NSWorkspace rejected static desktop: ' + error[0]);\n        var actual = ws.desktopImageURLForScreen(screens.objectAtIndex(i));\n        if (!actual || ObjC.unwrap(actual.absoluteString) !== ObjC.unwrap(url.absoluteString))\n            throw new Error('NSWorkspace desktop image readback mismatch');\n    }\n    var error = Ref();\n    if (!fm.createDirectoryAtPathWithIntermediateDirectoriesAttributesError($(folder), true, null, error))\n        throw new Error('Cannot create per-user NABU state');\n    if (!$('static-desktop-v1\\n').writeToFileAtomicallyEncodingError($(marker), true, $.NSUTF8StringEncoding, error))\n        throw new Error('Cannot commit desktop setup marker');\n    return 'NABU static desktop configured and URL read back';\n}\n";
    static const char agent[]="<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n<plist version=\"1.0\"><dict>\n<key>Label</key><string>org.unixnu.nabu.static-desktop</string>\n<key>ProgramArguments</key><array><string>/usr/bin/osascript</string><string>-l</string><string>JavaScript</string><string>/System/Volumes/Data/Library/Application Support/NABU/Desktop/set-desktop.js</string></array>\n<key>RunAtLoad</key><true/>\n<key>LimitLoadToSessionType</key><string>Aqua</string>\n<key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>\n<key>ThrottleInterval</key><integer>30</integer>\n</dict></plist>\n";
    snprintf(path,sizeof path,"%s/Library/Application Support/NABU/Desktop/desktop.png",NABU_DESKTOP_DATA_ROOT);
    if(nabu_desktop_write(path,image,sizeof image))return -1;
    snprintf(path,sizeof path,"%s/Library/Application Support/NABU/Desktop/set-desktop.js",NABU_DESKTOP_DATA_ROOT);
    if(nabu_desktop_write(path,script,sizeof script-1))return -1;
    snprintf(path,sizeof path,"%s/Library/LaunchAgents/org.unixnu.nabu.static-desktop.plist",NABU_DESKTOP_DATA_ROOT);
    if(nabu_desktop_write(path,agent,sizeof agent-1))return -1;
    fprintf(stderr,"NABU-DESKTOP: static PNG and Aqua LaunchAgent installed; session API/rendering pending\n");
    return 0;
}
#endif
