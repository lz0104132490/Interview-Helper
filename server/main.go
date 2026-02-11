package main

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
	"github.com/google/uuid"
	"github.com/joho/godotenv"
	"github.com/skip2/go-qrcode"
)

type feedbackRequest struct {
	Feedback  string                 `json:"feedback"`
	Image     string                 `json:"image"`
	Timestamp string                 `json:"timestamp"`
	Meta      map[string]interface{} `json:"meta"`
}

type feedbackPayload struct {
	ID           string                 `json:"id"`
	Timestamp    string                 `json:"timestamp"`
	Feedback     string                 `json:"feedback"`
	ScreenshotID string                 `json:"screenshotId"`
	Screenshot   string                 `json:"screenshotUrl"`
	Meta         map[string]interface{} `json:"meta"`
}

type controlRequest struct {
	Action string `json:"action"`
	Delta  int    `json:"delta"`
}

type state struct {
	mu          sync.RWMutex
	latest      *feedbackPayload
	latestBytes []byte
}

func (s *state) setLatest(payload *feedbackPayload) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.latest = payload
	bytes, _ := json.Marshal(payload)
	s.latestBytes = bytes
}

func (s *state) getLatest() (*feedbackPayload, []byte) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	if s.latest == nil {
		return nil, nil
	}
	return s.latest, append([]byte(nil), s.latestBytes...)
}

type broker struct {
	mu      sync.Mutex
	clients map[chan []byte]struct{}
}

func newBroker() *broker {
	return &broker{
		clients: make(map[chan []byte]struct{}),
	}
}

func (b *broker) addClient(ch chan []byte) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.clients[ch] = struct{}{}
}

func (b *broker) removeClient(ch chan []byte) {
	b.mu.Lock()
	defer b.mu.Unlock()
	delete(b.clients, ch)
	close(ch)
}

func (b *broker) broadcast(payload []byte) {
	b.mu.Lock()
	defer b.mu.Unlock()
	for ch := range b.clients {
		select {
		case ch <- payload:
		default:
			// drop instead of blocking slow clients
		}
	}
}

var (
	dataURLPattern = regexp.MustCompile(`^data:image/(png|jpeg);base64,(.+)$`)
)

func main() {
	_ = godotenv.Load()

	port := os.Getenv("PORT")
	if port == "" {
		port = "4000"
	}

	publicDir := filepath.Join(".", "public")
	uploadDir := filepath.Join(".", "uploads")

	if err := os.MkdirAll(uploadDir, 0o755); err != nil {
		log.Fatalf("failed to create uploads directory: %v", err)
	}

	state := &state{}
	broker := newBroker()

	r := chi.NewRouter()
	r.Use(middleware.RequestID)
	r.Use(middleware.RealIP)
	r.Use(middleware.Logger)
	r.Use(middleware.Recoverer)
	r.Use(corsMiddleware())

	r.Post("/api/feedback", handleFeedback(uploadDir, state, broker))
	r.Get("/api/latest", handleLatest(state))
	r.Get("/api/stream", handleStream(state, broker))
	r.Post("/api/control", handleControl(broker))
	r.Get("/api/info", handleInfo(port))
	r.Get("/api/qr", handleQR(port))

	r.Handle("/uploads/*", http.StripPrefix("/uploads/", cacheControlFileServer(uploadDir, 300)))

	r.NotFound(spaHandler(publicDir))

	log.Printf("Interview relay server listening on :%s", port)
	if err := http.ListenAndServe(":"+port, r); err != nil {
		log.Fatal(err)
	}
}

func handleFeedback(uploadDir string, s *state, b *broker) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var body feedbackRequest
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			http.Error(w, "invalid JSON payload", http.StatusBadRequest)
			return
		}

		isAudio := false
		if body.Meta != nil {
			if mode, ok := body.Meta["mode"].(string); ok && mode == "audio" {
				isAudio = true
			}
		}

		if strings.TrimSpace(body.Feedback) == "" {
			http.Error(w, "feedback is required", http.StatusBadRequest)
			return
		}
		if body.Image == "" && !isAudio {
			http.Error(w, "image is required", http.StatusBadRequest)
			return
		}

		filename := ""
		if body.Image != "" {
			var err error
			filename, err = persistScreenshot(uploadDir, body.Image)
			if err != nil {
				http.Error(w, fmt.Sprintf("invalid image: %v", err), http.StatusBadRequest)
				return
			}
		}

		if body.Timestamp == "" {
			body.Timestamp = time.Now().UTC().Format(time.RFC3339)
		}
		if body.Meta == nil {
			body.Meta = map[string]interface{}{}
		}

		screenshotURL := ""
		if filename != "" {
			screenshotURL = "/uploads/" + filename
		}

		payload := &feedbackPayload{
			ID:           uuid.NewString(),
			Timestamp:    body.Timestamp,
			Feedback:     body.Feedback,
			ScreenshotID: filename,
			Screenshot:   screenshotURL,
			Meta:         body.Meta,
		}

		s.setLatest(payload)
		bytes, _ := json.Marshal(payload)
		b.broadcast(bytes)

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		if _, err := w.Write(bytes); err != nil {
			log.Printf("failed to write response: %v", err)
		}
	}
}

func handleLatest(s *state) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		payload, _ := s.getLatest()
		if payload == nil {
			http.Error(w, "no feedback yet", http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		if err := json.NewEncoder(w).Encode(payload); err != nil {
			log.Printf("failed to encode latest payload: %v", err)
		}
	}
}

func handleStream(s *state, b *broker) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		flusher, ok := w.(http.Flusher)
		if !ok {
			http.Error(w, "streaming unsupported", http.StatusInternalServerError)
			return
		}

		w.Header().Set("Content-Type", "text/event-stream")
		w.Header().Set("Cache-Control", "no-cache")
		w.Header().Set("Connection", "keep-alive")

		client := make(chan []byte, 4)
		b.addClient(client)
		defer b.removeClient(client)

		if _, latestBytes := s.getLatest(); len(latestBytes) > 0 {
			if _, err := fmt.Fprintf(w, "data: %s\n\n", latestBytes); err == nil {
				flusher.Flush()
			}
		}

		notify := r.Context().Done()
		for {
			select {
			case <-notify:
				return
			case payload := <-client:
				if _, err := fmt.Fprintf(w, "data: %s\n\n", payload); err != nil {
					return
				}
				flusher.Flush()
			}
		}
	}
}

func handleControl(b *broker) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var body controlRequest
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			http.Error(w, "invalid JSON payload", http.StatusBadRequest)
			return
		}
		if body.Action != "scroll" {
			http.Error(w, "unsupported action", http.StatusBadRequest)
			return
		}
		if body.Delta == 0 {
			http.Error(w, "delta is required", http.StatusBadRequest)
			return
		}
		if body.Delta > 2000 {
			body.Delta = 2000
		}
		if body.Delta < -2000 {
			body.Delta = -2000
		}

		payload := map[string]interface{}{
			"type":      "control",
			"action":    body.Action,
			"delta":     body.Delta,
			"timestamp": time.Now().UTC().Format(time.RFC3339),
		}
		bytes, _ := json.Marshal(payload)
		b.broadcast(bytes)

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusAccepted)
		if _, err := w.Write(bytes); err != nil {
			log.Printf("failed to write control response: %v", err)
		}
	}
}

func handleInfo(port string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		hostname, _ := os.Hostname()
		payload := map[string]interface{}{
			"hostname":    hostname,
			"urls":        localBaseURLs(port),
			"generatedAt": time.Now().UTC().Format(time.RFC3339),
		}

		w.Header().Set("Content-Type", "application/json")
		if err := json.NewEncoder(w).Encode(payload); err != nil {
			log.Printf("failed to encode info payload: %v", err)
		}
	}
}

func handleQR(port string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		target := strings.TrimSpace(r.URL.Query().Get("target"))
		var err error

		if target == "" {
			urls := localBaseURLs(port)
			if len(urls) == 0 {
				http.Error(w, "no LAN URLs found", http.StatusNotFound)
				return
			}
			target = urls[0]
		} else {
			target, err = sanitizeTarget(target)
			if err != nil {
				http.Error(w, "invalid target", http.StatusBadRequest)
				return
			}
		}

		img, err := qrcode.Encode(target, qrcode.Medium, 256)
		if err != nil {
			http.Error(w, "failed to create QR code", http.StatusInternalServerError)
			return
		}

		w.Header().Set("Content-Type", "image/png")
		w.Header().Set("Cache-Control", "no-store")
		if _, err := w.Write(img); err != nil {
			log.Printf("failed to write QR payload: %v", err)
		}
	}
}

func persistScreenshot(dir, dataURL string) (string, error) {
	matches := dataURLPattern.FindStringSubmatch(dataURL)
	if len(matches) != 3 {
		return "", errors.New("expected data:image/(png|jpeg);base64,... format")
	}
	ext := matches[1]
	if ext == "jpeg" {
		ext = "jpg"
	}

	decoded, err := base64.StdEncoding.DecodeString(matches[2])
	if err != nil {
		return "", fmt.Errorf("decode: %w", err)
	}

	filename := fmt.Sprintf("%d-%s.%s", time.Now().UnixMilli(), uuid.NewString()[:8], ext)
	path := filepath.Join(dir, filename)

	if err := os.WriteFile(path, decoded, 0o644); err != nil {
		return "", fmt.Errorf("write: %w", err)
	}

	return filename, nil
}

func localBaseURLs(port string) []string {
	var urls []string
	seen := make(map[string]struct{})

	add := func(u string) {
		if u == "" {
			return
		}
		if _, ok := seen[u]; ok {
			return
		}
		seen[u] = struct{}{}
		urls = append(urls, u)
	}

	add(fmt.Sprintf("http://localhost:%s", port))
	if hostname, err := os.Hostname(); err == nil && hostname != "" {
		add(fmt.Sprintf("http://%s:%s", hostname, port))
		add(fmt.Sprintf("http://%s.local:%s", hostname, port))
	}

	ifaces, err := net.Interfaces()
	if err != nil {
		return urls
	}

	for _, iface := range ifaces {
		if (iface.Flags&net.FlagUp) == 0 || (iface.Flags&net.FlagLoopback) != 0 {
			continue
		}

		addrs, err := iface.Addrs()
		if err != nil {
			continue
		}

		for _, addr := range addrs {
			var ip net.IP
			switch v := addr.(type) {
			case *net.IPNet:
				ip = v.IP
			case *net.IPAddr:
				ip = v.IP
			}

			if ip == nil || ip.IsLoopback() {
				continue
			}
			ip = ip.To4()
			if ip == nil {
				continue
			}
			if !ip.IsPrivate() && !ip.IsGlobalUnicast() {
				continue
			}

			add(fmt.Sprintf("http://%s:%s", ip.String(), port))
		}
	}

	return urls
}

func sanitizeTarget(target string) (string, error) {
	target = strings.TrimSpace(target)
	if target == "" {
		return "", errors.New("empty target")
	}

	parsed, err := url.ParseRequestURI(target)
	if err != nil {
		return "", err
	}

	scheme := strings.ToLower(parsed.Scheme)
	if scheme != "http" && scheme != "https" {
		return "", errors.New("unsupported scheme")
	}

	return parsed.String(), nil
}

func spaHandler(publicDir string) http.HandlerFunc {
	fileServer := http.FileServer(http.Dir(publicDir))
	return func(w http.ResponseWriter, r *http.Request) {
		requestPath := filepath.Clean(r.URL.Path)
		if requestPath == "/" {
			http.ServeFile(w, r, filepath.Join(publicDir, "index.html"))
			return
		}

		fullPath := filepath.Join(publicDir, strings.TrimPrefix(requestPath, "/"))
		if info, err := os.Stat(fullPath); err == nil && !info.IsDir() {
			fileServer.ServeHTTP(w, r)
			return
		}
		http.ServeFile(w, r, filepath.Join(publicDir, "index.html"))
	}
}

func cacheControlFileServer(dir string, maxAge int) http.Handler {
	fs := http.FileServer(http.Dir(dir))
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", fmt.Sprintf("public, max-age=%d", maxAge))
		fs.ServeHTTP(w, r)
	})
}

func corsMiddleware() func(http.Handler) http.Handler {
	allowedOrigin := os.Getenv("CLIENT_ORIGIN")
	if allowedOrigin == "" {
		allowedOrigin = "*"
	}

	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Access-Control-Allow-Origin", allowedOrigin)
			w.Header().Set("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
			w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization")
			w.Header().Set("Access-Control-Allow-Credentials", "false")

			if r.Method == http.MethodOptions {
				w.WriteHeader(http.StatusNoContent)
				return
			}

			next.ServeHTTP(w, r)
		})
	}
}
