//! CPU, memory and network for the sidebar. The Python service reports the same
//! (plus the GPU); this one also works while the runtime is being installed.

use std::sync::Mutex;
use std::time::Instant;

use serde_json::{json, Value};
use sysinfo::{Networks, System};

pub struct Load(Mutex<State>);

struct State {
    sys: Option<System>,
    nets: Option<Networks>,
    at: Instant,
}

impl Default for Load {
    fn default() -> Self {
        let mut sys = System::new().ok();
        if let Some(s) = sys.as_mut() {
            s.refresh_cpu_usage(); // the first reading only sets the baseline
        }
        Load(Mutex::new(State { sys, nets: Networks::new_with_refreshed_list().ok(), at: Instant::now() }))
    }
}

impl Load {
    pub fn sample(&self) -> Value {
        let mut guard = self.0.lock().unwrap();
        let s = &mut *guard;
        let dt = s.at.elapsed().as_secs_f64().max(1e-3);
        s.at = Instant::now();
        let net = s.nets.as_mut().map(|n| {
            n.refresh(true);
            let (up, down) = n.iter().fold((0u64, 0u64), |(u, d), (_, x)| (u + x.transmitted(), d + x.received()));
            json!({ "up": up as f64 / dt, "down": down as f64 / dt })
        });
        let Some(sys) = s.sys.as_mut() else { return json!({ "cpu": null, "ram": null, "gpu": null, "net": net }) };
        sys.refresh_cpu_usage();
        sys.refresh_memory();
        json!({
            "cpu": sys.global_cpu_usage(),
            "cpu_count": sys.cpus().len(),
            "ram": { "used": sys.used_memory(), "total": sys.total_memory() },
            "gpu": null,
            "net": net,
        })
    }
}
