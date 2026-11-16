// SPDX-License-Identifier: BSD-2-Clause-Patent
// Keep an existing firmware loader resident. Only the protocol interface is
// replaced, and unloading the ESP driver restores that exact interface.
typedef struct {
  EFI_HANDLE               Handle;
  UNIXNU_LOADER_PROTOCOL    *Previous;
} DARWIN_LOADER_REGISTRATION;

STATIC
EFI_STATUS
DarwinRegisterLoader (
  EFI_HANDLE                  Image,
  UNIXNU_LOADER_PROTOCOL       *Loader,
  BOOLEAN                     Replace,
  DARWIN_LOADER_REGISTRATION   *Registration
  )
{
  EFI_STATUS                Status;
  EFI_HANDLE                *Handles;
  EFI_HANDLE                Handle;
  UINTN                     Count;
  UNIXNU_LOADER_PROTOCOL     *Previous;

  if (Registration->Handle != NULL) { return EFI_ALREADY_STARTED; }
  Handles = NULL;
  Count = 0;
  Status = gBS->LocateHandleBuffer (ByProtocol, &gUniXNULoaderProtocolGuid, NULL, &Count, &Handles);
  if (Status == EFI_NOT_FOUND) {
    Handle = Image;
    Status = gBS->InstallMultipleProtocolInterfaces (&Handle, &gUniXNULoaderProtocolGuid, Loader, NULL);
    if (!EFI_ERROR (Status)) { Registration->Handle = Handle; }
    return Status;
  }
  if (EFI_ERROR (Status)) { return Status; }
  if (!Replace || (Count != 1)) {
    FreePool (Handles);
    return EFI_ALREADY_STARTED;
  }
  Handle = Handles[0];
  FreePool (Handles);
  Status = gBS->HandleProtocol (Handle, &gUniXNULoaderProtocolGuid, (VOID **)&Previous);
  if (EFI_ERROR (Status)) { return Status; }
  if ((Previous == NULL) || (Previous->Boot == NULL) ||
      ((Previous->Revision != UNIXNU_LOADER_REVISION_1) && (Previous->Revision != UNIXNU_LOADER_REVISION))) {
    return EFI_UNSUPPORTED;
  }
  Status = gBS->ReinstallProtocolInterface (Handle, &gUniXNULoaderProtocolGuid, Previous, Loader);
  if (!EFI_ERROR (Status)) {
    Registration->Handle = Handle;
    Registration->Previous = Previous;
  }
  return Status;
}

STATIC
EFI_STATUS
DarwinUnregisterLoader (DARWIN_LOADER_REGISTRATION *Registration, UNIXNU_LOADER_PROTOCOL *Loader)
{
  EFI_STATUS                Status;
  UNIXNU_LOADER_PROTOCOL     *Current;

  if (Registration->Handle == NULL) { return EFI_NOT_STARTED; }
  Status = gBS->HandleProtocol (Registration->Handle, &gUniXNULoaderProtocolGuid, (VOID **)&Current);
  if (EFI_ERROR (Status)) { return Status; }
  if (Current != Loader) { return EFI_ACCESS_DENIED; }
  if (Registration->Previous != NULL) {
    Status = gBS->ReinstallProtocolInterface (Registration->Handle, &gUniXNULoaderProtocolGuid,
                                            Loader, Registration->Previous);
  } else {
    Status = gBS->UninstallMultipleProtocolInterfaces (Registration->Handle, &gUniXNULoaderProtocolGuid,
                                                     Loader, NULL);
  }
  if (!EFI_ERROR (Status)) { ZeroMem (Registration, sizeof (*Registration)); }
  return Status;
}
