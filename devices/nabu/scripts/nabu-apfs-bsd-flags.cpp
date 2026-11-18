// SPDX-License-Identifier: GPL-2.0-or-later
// Read native inode metadata through the read-only apfs-fuse library.
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <map>
#include <memory>
#include <string>
#include "ApfsLib/Device.h"
#include "ApfsLib/ApfsContainer.h"
#include "ApfsLib/ApfsVolume.h"
#include "ApfsLib/ApfsDir.h"
#include "ApfsLib/Util.h"
int main(int argc, char **argv) {
    if (argc != 3) return 2;
    char *end = nullptr;
    const uint64_t offset = std::strtoull(argv[2], &end, 0);
    if (!end || *end) return 2;
    std::unique_ptr<Device> device(Device::OpenDevice(argv[1]));
    if (!device || offset >= device->GetSize()) return 1;
    ApfsContainer container(device.get(), offset, device->GetSize() - offset);
    if (!container.Init()) return 1;
    std::unique_ptr<ApfsVolume> volume(container.GetVolume(0));
    if (!volume) return 1;
    ApfsDir directory(*volume);
    std::map<std::string, uint64_t> cached{{"", 2}};
    std::string path;
    while (std::getline(std::cin, path)) {
        if (path.empty() || path[0] != '/' || path.find('\t') != std::string::npos) return 2;
        uint64_t inode = 2;
        std::string prefix;
        size_t begin = 1;
        while (begin < path.size()) {
            const size_t next = path.find('/', begin);
            std::string name = path.substr(begin, next == std::string::npos ? next : next - begin);
            if (name.empty() || name == "." || name == "..") return 2;
            prefix += "/" + name;
            auto known = cached.find(prefix);
            if (known != cached.end()) inode = known->second;
            else {
                ApfsDir::DirRec entry;
                if (!directory.LookupName(entry, inode, name.c_str())) {
                    std::cerr << "Missing APFS metadata " << prefix << '\n'; return 1;
                }
                inode = entry.file_id;
                cached[prefix] = inode;
            }
            if (next == std::string::npos) break;
            begin = next + 1;
        }
        ApfsDir::Inode record;
        if (!directory.GetInode(record, inode)) return 1;
        std::cout << std::hex << record.bsd_flags << '\t' << path << '\n';
    }
    return 0;
}
