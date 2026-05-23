VERSION ?= 1.0.2
DIST := ../backend/agent_dist
LDFLAGS := -s -w -X main.Version=$(VERSION)

.PHONY: all clean deps amd64 arm64 armv7

all: deps amd64 arm64 armv7

deps:
	go mod tidy
	@mkdir -p $(DIST)

amd64:
	GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -ldflags="$(LDFLAGS)" -o $(DIST)/mesh-agent-amd64 .

arm64:
	GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go build -ldflags="$(LDFLAGS)" -o $(DIST)/mesh-agent-arm64 .

armv7:
	GOOS=linux GOARCH=arm GOARM=7 CGO_ENABLED=0 go build -ldflags="$(LDFLAGS)" -o $(DIST)/mesh-agent-armv7 .

clean:
	rm -f $(DIST)/socks-agent-* $(DIST)/mesh-agent-*
