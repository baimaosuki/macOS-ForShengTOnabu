// SPDX-License-Identifier: BSD-2-Clause-Patent
// Run against real UEFI boot services under QEMU; never enter a kernel.
#include <Uefi.h>
#include <Protocol/LoadedImage.h>
#include <Protocol/UniXNULoader.h>
#include <Library/BaseLib.h>
#include <Library/BaseMemoryLib.h>
#include <Library/DevicePathLib.h>
#include <Library/MemoryAllocationLib.h>
#include <Library/UefiBootServicesTableLib.h>
#include <Library/UefiRuntimeServicesTableLib.h>
#include <Library/UefiLib.h>

STATIC UINTN mOldCalls;
STATIC EFI_HANDLE mVolume;

STATIC VOID Stop (BOOLEAN Ok, CONST CHAR16 *What)
{
  Print (L"ESP-LOADER-TEST: %s %s\n", Ok ? L"PASS" : L"FAIL", What);
  if (!Ok) { gRT->ResetSystem (EfiResetShutdown, EFI_ABORTED, 0, NULL); CpuDeadLoop (); }
}

STATIC EFI_STATUS EFIAPI OldBoot (UNIXNU_LOADER_PROTOCOL *This, CONST UNIXNU_LOADER_BOOT_REQUEST *Request)
{
  (VOID)This; (VOID)Request;
  mOldCalls++;
  Stop (FALSE, L"old loader was called");
  return EFI_ABORTED;
}

STATIC UNIXNU_LOADER_PROTOCOL mOld = { UNIXNU_LOADER_REVISION_1, OldBoot };
STATIC UNIXNU_LOADER_PROTOCOL mOther = { UNIXNU_LOADER_REVISION, OldBoot };

STATIC EFI_STATUS Start (CONST CHAR16 *Path, BOOLEAN Replace, EFI_HANDLE *Image)
{
  EFI_DEVICE_PATH_PROTOCOL *DevicePath;
  EFI_LOADED_IMAGE_PROTOCOL *Loaded;
  EFI_STATUS Status;
  STATIC CHAR16 Options[] = L"replace-existing";

  DevicePath = FileDevicePath (mVolume, Path);
  if (DevicePath == NULL) { return EFI_OUT_OF_RESOURCES; }
  Status = gBS->LoadImage (FALSE, gImageHandle, DevicePath, NULL, 0, Image);
  FreePool (DevicePath);
  if (EFI_ERROR (Status)) { return Status; }
  if (Replace) {
    Status = gBS->HandleProtocol (*Image, &gEfiLoadedImageProtocolGuid, (VOID **)&Loaded);
    if (EFI_ERROR (Status)) { return Status; }
    Loaded->LoadOptions = Options;
    Loaded->LoadOptionsSize = sizeof (Options);
  }
  return gBS->StartImage (*Image, NULL, NULL);
}

STATIC VOID EFIAPI FinishRequired (EFI_EVENT Event, VOID *Context)
{
  (VOID)Event; (VOID)Context;
  Stop (mOldCalls == 0, L"required driver failure kept old loader uncalled");
  gRT->ResetSystem (EfiResetShutdown, EFI_SUCCESS, 0, NULL);
  CpuDeadLoop ();
}

EFI_STATUS EFIAPI UefiMain (EFI_HANDLE Image, EFI_SYSTEM_TABLE *SystemTable)
{
  EFI_STATUS Status;
  EFI_LOADED_IMAGE_PROTOCOL *Loaded;
  EFI_HANDLE OldHandle = NULL, OtherHandle = NULL, Driver;
  UNIXNU_LOADER_PROTOCOL *Current;
  UNIXNU_LOADER_BOOT_REQUEST Request;
  EFI_EVENT Timer;
  UINTN Index;

  (VOID)SystemTable;
  Status = gBS->HandleProtocol (Image, &gEfiLoadedImageProtocolGuid, (VOID **)&Loaded);
  Stop (!EFI_ERROR (Status), L"test boot volume");
  mVolume = Loaded->DeviceHandle;
  Status = gBS->InstallMultipleProtocolInterfaces (&OldHandle, &gUniXNULoaderProtocolGuid, &mOld, NULL);
  Stop (!EFI_ERROR (Status), L"firmware loader fixture installed");
  Status = Start (L"\\EFI\\UniXNU\\Drivers\\DarwinBoot.efi", FALSE, &Driver);
  Stop (Status == EFI_ALREADY_STARTED, L"replacement requires explicit option");
  for (Index = 0; Index < 2; Index++) {
    mOld.Revision = Index == 0 ? UNIXNU_LOADER_REVISION_1 : UNIXNU_LOADER_REVISION;
    Status = Start (L"\\EFI\\UniXNU\\Drivers\\DarwinBoot.efi", TRUE, &Driver);
    Stop (!EFI_ERROR (Status), L"replacement driver started");
    Status = gBS->LocateProtocol (&gUniXNULoaderProtocolGuid, NULL, (VOID **)&Current);
    Stop (!EFI_ERROR (Status) && Current != &mOld && Current->Revision == UNIXNU_LOADER_REVISION,
          L"LocateProtocol selected ESP loader");
    ZeroMem (&Request, sizeof (Request));
    Status = Current->Boot (Current, &Request);
    Stop (Status == EFI_INVALID_PARAMETER && mOldCalls == 0, L"new Boot callback received request");
    Status = gBS->UnloadImage (Driver);
    Stop (!EFI_ERROR (Status), L"ESP driver unloaded");
    Status = gBS->LocateProtocol (&gUniXNULoaderProtocolGuid, NULL, (VOID **)&Current);
    Stop (!EFI_ERROR (Status) && Current == &mOld, L"original firmware loader restored");
  }
  mOld.Revision = 0x30000;
  Status = Start (L"\\EFI\\UniXNU\\Drivers\\DarwinBoot.efi", TRUE, &Driver);
  Stop (Status == EFI_UNSUPPORTED, L"unknown protocol revision retained");
  mOld.Revision = UNIXNU_LOADER_REVISION;
  Status = gBS->InstallMultipleProtocolInterfaces (&OtherHandle, &gUniXNULoaderProtocolGuid, &mOther, NULL);
  Stop (!EFI_ERROR (Status), L"duplicate loader fixture installed");
  Status = Start (L"\\EFI\\UniXNU\\Drivers\\DarwinBoot.efi", TRUE, &Driver);
  Stop (Status == EFI_ALREADY_STARTED, L"ambiguous providers left intact");
  gBS->UninstallMultipleProtocolInterfaces (OtherHandle, &gUniXNULoaderProtocolGuid, &mOther, NULL);
  gBS->UninstallMultipleProtocolInterfaces (OldHandle, &gUniXNULoaderProtocolGuid, &mOld, NULL);
  Status = Start (L"\\EFI\\UniXNU\\Drivers\\DarwinBoot.efi", TRUE, &Driver);
  Stop (!EFI_ERROR (Status), L"standalone installation without firmware loader");
  Status = gBS->UnloadImage (Driver);
  Stop (!EFI_ERROR (Status), L"standalone uninstall");
  Status = gBS->LocateProtocol (&gUniXNULoaderProtocolGuid, NULL, (VOID **)&Current);
  Stop (Status == EFI_NOT_FOUND, L"no stale loader after uninstall");
  OldHandle = NULL;
  gBS->InstallMultipleProtocolInterfaces (&OldHandle, &gUniXNULoaderProtocolGuid, &mOld, NULL);
  Status = gBS->CreateEvent (EVT_TIMER | EVT_NOTIFY_SIGNAL, TPL_CALLBACK, FinishRequired, NULL, &Timer);
  Stop (!EFI_ERROR (Status), L"required driver test timer");
  gBS->SetTimer (Timer, TimerRelative, 8 * 10000000);
  Status = Start (L"\\EFI\\UniXNU\\UniXNU.efi", FALSE, &Driver);
  Stop (FALSE, L"menu returned before required driver validation");
  return Status;
}
