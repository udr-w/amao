// CASE STUDY -- deliberately NOT wired into amao's real pipeline.
// A C++ port of src/amao/git_helper.py's _validate_diff/_validate_path --
// the single most security-critical function in amao (it's the only thing
// standing between an LLM-authored diff and path traversal / symlink /
// absolute-path escapes out of the target project directory). See
// NATIVE_EXTENSIONS.md for why this stays a case study, not production code:
// re-implementing security-critical validation in a second language is a
// much higher bar than "the fuzz test passed" -- exact edge-case agreement
// between Python's Path.resolve(strict=False) and C++'s
// std::filesystem::weakly_canonical is genuinely subtle, and this file is
// the evidence for that claim, not a replacement for the real validator.
//
// Returns an empty string when the diff is safe, or a (Python-message-
// independent) short reason code when it isn't -- differential testing only
// compares "did both sides agree it's unsafe," never exact wording, since
// message text was never a security boundary.
#include <pybind11/pybind11.h>

#include <filesystem>
#include <regex>
#include <string>
#include <vector>

namespace py = pybind11;
namespace fs = std::filesystem;

namespace {

const std::regex kDiffGitRe(R"(^diff --git a/(.+) b/(.+)$)");
const std::regex kOldPathRe(R"(^--- (?:a/(.+)|/dev/null)$)");
const std::regex kNewPathRe(R"(^\+\+\+ (?:b/(.+)|/dev/null)$)");
const std::regex kRenameFromRe(R"(^rename from (.+)$)");
const std::regex kRenameToRe(R"(^rename to (.+)$)");
const std::regex kCopyFromRe(R"(^copy from (.+)$)");
const std::regex kCopyToRe(R"(^copy to (.+)$)");
const std::regex kModeRe(R"(^(?:old mode|new mode|new file mode|deleted file mode) (\d+)$)");
const std::string kSymlinkMode = "120000";
const std::vector<std::string> kBinaryMarkers = {"GIT binary patch", "Binary files "};

std::string validate_path(const std::string &path, const fs::path &repo_root) {
    if (path == "/dev/null") {
        return "";
    }
    if (!path.empty() && path[0] == '/') {
        return "absolute_path";
    }
    // Python's `any(part == ".." for part in Path(path).parts)` -- split on
    // '/' and check each segment, matching Path's own component semantics
    // closely enough for this check (both treat repeated slashes/'.' the
    // same way for the purpose of spotting a literal ".." segment).
    std::stringstream ss(path);
    std::string segment;
    while (std::getline(ss, segment, '/')) {
        if (segment == "..") {
            return "path_traversal";
        }
    }

    std::error_code ec;
    fs::path resolved = fs::weakly_canonical(repo_root / path, ec);
    if (ec) {
        return "resolve_error";
    }
    fs::path repo_root_canonical = fs::weakly_canonical(repo_root, ec);
    if (ec) {
        return "resolve_error";
    }
    if (resolved == repo_root_canonical) {
        return "";
    }
    // "repo_root not in resolved.parents" in Python terms:
    auto it = resolved.begin();
    bool is_descendant = false;
    fs::path prefix;
    for (const auto &part : resolved) {
        prefix /= part;
        if (prefix == repo_root_canonical) {
            is_descendant = true;
            break;
        }
    }
    (void)it;
    if (!is_descendant) {
        return "escapes_directory";
    }
    return "";
}

}  // namespace

// Returns "" if safe, otherwise a short reason code (see validate_path).
std::string validate_diff(const std::string &diff_text, const std::string &repo_dir) {
    std::string trimmed = diff_text;
    size_t start = trimmed.find_first_not_of(" \t\r\n");
    if (start == std::string::npos) {
        return "empty_diff";
    }

    for (const auto &marker : kBinaryMarkers) {
        if (diff_text.find(marker) != std::string::npos) {
            return "binary_content";
        }
    }

    std::error_code ec;
    fs::path repo_root = fs::weakly_canonical(fs::path(repo_dir), ec);
    if (ec) {
        return "resolve_error";
    }

    std::stringstream stream(diff_text);
    std::string line;
    while (std::getline(stream, line)) {
        if (!line.empty() && line.back() == '\r') {
            line.pop_back();
        }

        std::smatch m;
        if (std::regex_match(line, m, kModeRe) && m[1].str() == kSymlinkMode) {
            return "symlink_mode";
        }

        if (std::regex_match(line, m, kDiffGitRe)) {
            std::string r1 = validate_path(m[1].str(), repo_root);
            if (!r1.empty()) return r1;
            std::string r2 = validate_path(m[2].str(), repo_root);
            if (!r2.empty()) return r2;
            continue;
        }

        for (const auto &re : {kRenameFromRe, kRenameToRe, kCopyFromRe, kCopyToRe}) {
            if (std::regex_match(line, m, re)) {
                std::string r = validate_path(m[1].str(), repo_root);
                if (!r.empty()) return r;
            }
        }

        if (std::regex_match(line, m, kOldPathRe) && m[1].matched) {
            std::string r = validate_path(m[1].str(), repo_root);
            if (!r.empty()) return r;
        }

        if (std::regex_match(line, m, kNewPathRe) && m[1].matched) {
            std::string r = validate_path(m[1].str(), repo_root);
            if (!r.empty()) return r;
        }
    }

    return "";
}

PYBIND11_MODULE(diff_validator, m) {
    m.doc() = "CASE STUDY ONLY -- not wired into amao's real pipeline. See NATIVE_EXTENSIONS.md.";
    m.def("validate_diff", &validate_diff, py::arg("diff_text"), py::arg("repo_dir"),
          "Returns '' if the diff is safe, else a short reason code.");
}
