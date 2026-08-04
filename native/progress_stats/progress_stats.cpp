// C++ port of the aggregation half of StateManager.get_progress_summary()
// (src/amao/state_manager.py) -- see NATIVE_EXTENSIONS.md. Deliberately
// scoped to the pure-computation part only: SQLite fetching and
// TIMESTAMP-string parsing stay in Python (nothing to gain moving those),
// this module receives already-parsed rows and does the counting/averaging.
//
// Exposed via pybind11 (a real C++ class + a std::vector<MilestoneRow>
// argument, auto-converted from a Python list) rather than plain ctypes --
// this is the "idiomatic modern C++ extension" example, in contrast to the
// duration/ ctypes demo's C-ABI-only scalar function.
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <optional>
#include <string>
#include <vector>

namespace py = pybind11;

struct MilestoneRow {
    std::string status;
    std::string title;
    int attempts;
    bool has_duration;
    double duration_seconds;
};

struct ProgressStats {
    int total = 0;
    int pending = 0;
    int in_progress = 0;
    int completed = 0;
    int halted = 0;
    std::optional<std::string> current_milestone_title;
    int current_milestone_attempts = 0;
    std::optional<double> average_completed_seconds;
    std::optional<double> estimated_remaining_seconds;
};

ProgressStats compute_progress_stats(const std::vector<MilestoneRow> &rows) {
    ProgressStats stats;
    stats.total = static_cast<int>(rows.size());

    double duration_sum = 0.0;
    int duration_count = 0;

    for (const auto &row : rows) {
        if (row.status == "PENDING") {
            stats.pending++;
        } else if (row.status == "IN_PROGRESS") {
            stats.in_progress++;
            if (!stats.current_milestone_title.has_value()) {
                stats.current_milestone_title = row.title;
                stats.current_milestone_attempts = row.attempts;
            }
        } else if (row.status == "COMPLETED") {
            stats.completed++;
        } else if (row.status == "HALTED") {
            stats.halted++;
        }

        if (row.has_duration) {
            duration_sum += row.duration_seconds;
            duration_count++;
        }
    }

    if (duration_count > 0) {
        double average = duration_sum / duration_count;
        stats.average_completed_seconds = average;
        stats.estimated_remaining_seconds = average * (stats.pending + stats.in_progress);
    }

    return stats;
}

PYBIND11_MODULE(progress_stats, m) {
    m.doc() = "Native aggregation for amao's get_progress_summary() -- see NATIVE_EXTENSIONS.md";

    py::class_<MilestoneRow>(m, "MilestoneRow")
        .def(py::init<>())
        .def(py::init<std::string, std::string, int, bool, double>(), py::arg("status"),
             py::arg("title"), py::arg("attempts"), py::arg("has_duration"),
             py::arg("duration_seconds"))
        .def_readwrite("status", &MilestoneRow::status)
        .def_readwrite("title", &MilestoneRow::title)
        .def_readwrite("attempts", &MilestoneRow::attempts)
        .def_readwrite("has_duration", &MilestoneRow::has_duration)
        .def_readwrite("duration_seconds", &MilestoneRow::duration_seconds);

    py::class_<ProgressStats>(m, "ProgressStats")
        .def(py::init<>())
        .def_readwrite("total", &ProgressStats::total)
        .def_readwrite("pending", &ProgressStats::pending)
        .def_readwrite("in_progress", &ProgressStats::in_progress)
        .def_readwrite("completed", &ProgressStats::completed)
        .def_readwrite("halted", &ProgressStats::halted)
        .def_readwrite("current_milestone_title", &ProgressStats::current_milestone_title)
        .def_readwrite("current_milestone_attempts", &ProgressStats::current_milestone_attempts)
        .def_readwrite("average_completed_seconds", &ProgressStats::average_completed_seconds)
        .def_readwrite("estimated_remaining_seconds", &ProgressStats::estimated_remaining_seconds);

    m.def("compute_progress_stats", &compute_progress_stats, py::arg("rows"),
          "Aggregate a list of MilestoneRow into a ProgressStats.");
}
