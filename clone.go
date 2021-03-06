package git

/*
#include <git2.h>

extern void _go_git_populate_clone_callbacks(git_clone_options *opts);
*/
import "C"
import (
	"errors"
	"runtime"
	"unsafe"
)

type RemoteCreateCallback func(repo *Repository, name, url string) (*Remote, ErrorCode)

type CloneOptions struct {
	*CheckoutOpts
	*FetchOptions
	Bare                 bool
	CheckoutBranch       string
	RemoteCreateCallback RemoteCreateCallback
}

func Clone(url string, path string, options *CloneOptions) (*Repository, error) {
	curl := C.CString(url)
	defer C.free(unsafe.Pointer(curl))

	cpath := C.CString(path)
	defer C.free(unsafe.Pointer(cpath))

	var err error
	cOptions := populateCloneOptions(&C.git_clone_options{}, options, &err)
	defer freeCloneOptions(cOptions)

	if len(options.CheckoutBranch) != 0 {
		cOptions.checkout_branch = C.CString(options.CheckoutBranch)
	}

	runtime.LockOSThread()
	defer runtime.UnlockOSThread()

	var ptr *C.git_repository
	ret := C.git_clone(&ptr, curl, cpath, cOptions)

	if ret == C.int(ErrorCodeUser) && err != nil {
		return nil, err
	}
	if ret < 0 {
		return nil, MakeGitError(ret)
	}

	return newRepositoryFromC(ptr), nil
}

//export remoteCreateCallback
func remoteCreateCallback(
	cremote unsafe.Pointer,
	crepo unsafe.Pointer,
	cname, curl *C.char,
	payload unsafe.Pointer,
) C.int {
	name := C.GoString(cname)
	url := C.GoString(curl)
	repo := newRepositoryFromC((*C.git_repository)(crepo))
	// We don't own this repository, so make sure we don't try to free it
	runtime.SetFinalizer(repo, nil)

	data, ok := pointerHandles.Get(payload).(*cloneCallbackData)
	if !ok {
		panic("invalid remote create callback")
	}

	remote, ret := data.options.RemoteCreateCallback(repo, name, url)
	// clear finalizer as the calling C function will
	// free the remote itself
	runtime.SetFinalizer(remote, nil)

	if ret < 0 {
		*data.errorTarget = errors.New(ErrorCode(ret).String())
		return C.int(ErrorCodeUser)
	}

	if remote == nil {
		panic("no remote created by callback")
	}

	cptr := (**C.git_remote)(cremote)
	*cptr = remote.ptr

	return C.int(ErrorCodeOK)
}

type cloneCallbackData struct {
	options     *CloneOptions
	errorTarget *error
}

func populateCloneOptions(ptr *C.git_clone_options, opts *CloneOptions, errorTarget *error) *C.git_clone_options {
	C.git_clone_options_init(ptr, C.GIT_CLONE_OPTIONS_VERSION)

	if opts == nil {
		return nil
	}
	populateCheckoutOptions(&ptr.checkout_opts, opts.CheckoutOpts, errorTarget)
	populateFetchOptions(&ptr.fetch_opts, opts.FetchOptions)
	ptr.bare = cbool(opts.Bare)

	if opts.RemoteCreateCallback != nil {
		data := &cloneCallbackData{
			options:     opts,
			errorTarget: errorTarget,
		}
		// Go v1.1 does not allow to assign a C function pointer
		C._go_git_populate_clone_callbacks(ptr)
		ptr.remote_cb_payload = pointerHandles.Track(data)
	}

	return ptr
}

func freeCloneOptions(ptr *C.git_clone_options) {
	if ptr == nil {
		return
	}

	freeCheckoutOptions(&ptr.checkout_opts)

	if ptr.remote_cb_payload != nil {
		pointerHandles.Untrack(ptr.remote_cb_payload)
	}

	C.free(unsafe.Pointer(ptr.checkout_branch))
}
