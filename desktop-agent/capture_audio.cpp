#include <audioclient.h>
#include <mmdeviceapi.h>
#include <windows.h>

#include <atomic>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <string>
#include <thread>

#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "mmdevapi.lib")
#pragma comment(lib, "avrt.lib")

namespace {

struct WavHeaderState {
    std::streampos riffSizePos;
    std::streampos dataSizePos;
    std::streampos dataStart;
};

bool WriteWavHeader(std::ofstream& out, const WAVEFORMATEX* format, WavHeaderState& state) {
    if (!out || !format) {
        return false;
    }

    uint32_t riffSize = 0;
    uint32_t dataSize = 0;
    uint32_t fmtSize = sizeof(WAVEFORMATEX) + format->cbSize;

    out.write("RIFF", 4);
    state.riffSizePos = out.tellp();
    out.write(reinterpret_cast<const char*>(&riffSize), sizeof(riffSize));
    out.write("WAVE", 4);
    out.write("fmt ", 4);
    out.write(reinterpret_cast<const char*>(&fmtSize), sizeof(fmtSize));
    out.write(reinterpret_cast<const char*>(format), fmtSize);
    out.write("data", 4);
    state.dataSizePos = out.tellp();
    out.write(reinterpret_cast<const char*>(&dataSize), sizeof(dataSize));
    state.dataStart = out.tellp();
    return true;
}

bool FinalizeWavHeader(std::ofstream& out, const WavHeaderState& state) {
    if (!out) {
        return false;
    }

    auto fileEnd = out.tellp();
    uint32_t dataSize = static_cast<uint32_t>(fileEnd - state.dataStart);
    uint32_t riffSize = static_cast<uint32_t>(fileEnd - std::streampos(8));

    out.seekp(state.riffSizePos);
    out.write(reinterpret_cast<const char*>(&riffSize), sizeof(riffSize));
    out.seekp(state.dataSizePos);
    out.write(reinterpret_cast<const char*>(&dataSize), sizeof(dataSize));
    out.seekp(fileEnd);
    return true;
}

}  // namespace

int main(int argc, char* argv[]) {
    std::string outputPath;
    for (int i = 1; i < argc; ++i) {
        if (std::string(argv[i]) == "--out" && i + 1 < argc) {
            outputPath = argv[++i];
        }
    }

    if (outputPath.empty()) {
        std::cerr << "Usage: capture_audio --out <path>\n";
        return 1;
    }

    HRESULT hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    if (FAILED(hr)) {
        std::cerr << "CoInitializeEx failed: " << std::hex << hr << "\n";
        return 1;
    }

    IMMDeviceEnumerator* enumerator = nullptr;
    IMMDevice* device = nullptr;
    IAudioClient* audioClient = nullptr;
    IAudioCaptureClient* captureClient = nullptr;
    WAVEFORMATEX* mixFormat = nullptr;
    HANDLE readyEvent = nullptr;

    hr = CoCreateInstance(
        __uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL,
        __uuidof(IMMDeviceEnumerator), reinterpret_cast<void**>(&enumerator));
    if (FAILED(hr)) {
        std::cerr << "CoCreateInstance failed: " << std::hex << hr << "\n";
        CoUninitialize();
        return 1;
    }

    hr = enumerator->GetDefaultAudioEndpoint(eRender, eConsole, &device);
    if (FAILED(hr)) {
        std::cerr << "GetDefaultAudioEndpoint failed: " << std::hex << hr << "\n";
        enumerator->Release();
        CoUninitialize();
        return 1;
    }

    hr = device->Activate(__uuidof(IAudioClient), CLSCTX_ALL, nullptr,
                          reinterpret_cast<void**>(&audioClient));
    if (FAILED(hr)) {
        std::cerr << "Activate IAudioClient failed: " << std::hex << hr << "\n";
        device->Release();
        enumerator->Release();
        CoUninitialize();
        return 1;
    }

    hr = audioClient->GetMixFormat(&mixFormat);
    if (FAILED(hr)) {
        std::cerr << "GetMixFormat failed: " << std::hex << hr << "\n";
        audioClient->Release();
        device->Release();
        enumerator->Release();
        CoUninitialize();
        return 1;
    }

    hr = audioClient->Initialize(
        AUDCLNT_SHAREMODE_SHARED,
        AUDCLNT_STREAMFLAGS_LOOPBACK,
        0,
        0,
        mixFormat,
        nullptr);
    if (FAILED(hr)) {
        std::cerr << "Initialize failed: " << std::hex << hr << "\n";
        CoTaskMemFree(mixFormat);
        audioClient->Release();
        device->Release();
        enumerator->Release();
        CoUninitialize();
        return 1;
    }

    hr = audioClient->GetService(__uuidof(IAudioCaptureClient),
                                 reinterpret_cast<void**>(&captureClient));
    if (FAILED(hr)) {
        std::cerr << "GetService failed: " << std::hex << hr << "\n";
        CoTaskMemFree(mixFormat);
        audioClient->Release();
        device->Release();
        enumerator->Release();
        CoUninitialize();
        return 1;
    }

    std::ofstream out(outputPath, std::ios::binary);
    WavHeaderState headerState{};
    if (!WriteWavHeader(out, mixFormat, headerState)) {
        std::cerr << "Failed to write WAV header.\n";
        captureClient->Release();
        CoTaskMemFree(mixFormat);
        audioClient->Release();
        device->Release();
        enumerator->Release();
        CoUninitialize();
        return 1;
    }

    hr = audioClient->Start();
    if (FAILED(hr)) {
        std::cerr << "Start failed: " << std::hex << hr << "\n";
        out.close();
        captureClient->Release();
        CoTaskMemFree(mixFormat);
        audioClient->Release();
        device->Release();
        enumerator->Release();
        CoUninitialize();
        return 1;
    }

    std::atomic<bool> stop{false};
    std::thread stdinThread([&stop]() {
        std::string line;
        std::getline(std::cin, line);
        stop = true;
    });

    while (!stop) {
        UINT32 packetLength = 0;
        hr = captureClient->GetNextPacketSize(&packetLength);
        if (FAILED(hr)) {
            break;
        }
        while (packetLength != 0) {
            BYTE* data = nullptr;
            UINT32 numFrames = 0;
            DWORD flags = 0;
            hr = captureClient->GetBuffer(&data, &numFrames, &flags, nullptr, nullptr);
            if (FAILED(hr)) {
                break;
            }

            UINT32 bytesToWrite = numFrames * mixFormat->nBlockAlign;
            if (flags & AUDCLNT_BUFFERFLAGS_SILENT) {
                std::string silence(bytesToWrite, '\0');
                out.write(silence.data(), static_cast<std::streamsize>(silence.size()));
            } else if (data && bytesToWrite > 0) {
                out.write(reinterpret_cast<const char*>(data), bytesToWrite);
            }

            captureClient->ReleaseBuffer(numFrames);
            hr = captureClient->GetNextPacketSize(&packetLength);
            if (FAILED(hr)) {
                break;
            }
        }
        Sleep(10);
    }

    audioClient->Stop();
    if (stdinThread.joinable()) {
        stdinThread.join();
    }

    FinalizeWavHeader(out, headerState);
    out.close();

    captureClient->Release();
    CoTaskMemFree(mixFormat);
    audioClient->Release();
    device->Release();
    enumerator->Release();
    CoUninitialize();
    return 0;
}
