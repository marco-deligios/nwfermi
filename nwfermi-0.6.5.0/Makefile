# build modules
obj-m := nw-fermi.o

# use the kernel build system
KERNEL_VERSION ?= $(shell uname -r)
KERNEL_SOURCE ?= /lib/modules/$(KERNEL_VERSION)/build

# build
PWD := $(CURDIR)
all module:
	$(MAKE) -C $(KERNEL_SOURCE) M=$(PWD) W=$(W) modules
clean:
	$(MAKE) -C $(KERNEL_SOURCE) M=$(PWD) W=$(W) clean

