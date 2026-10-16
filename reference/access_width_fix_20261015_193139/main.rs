use std::path::PathBuf;

use subgates::{Inv, Nand2, PrimitiveGateParams};
use subgeom::{Point, Rect};
use substrate::layout::cell::{Port, CellPort};
use sky130_open_pdk::Sky130OpenPdk;
use substrate::component::Component;
use substrate::data::{SubstrateConfig, SubstrateCtx};
use substrate::deps::arcstr::ArcStr;
use substrate::error::Result;
use substrate::layout::context::LayoutCtx;
use substrate::layout::elements::via::{Via, ViaExpansion, ViaParams};
use substrate::layout::layers::LayerBoundBox;
use substrate::layout::layers::selector::Selector;
use substrate::pdk::PdkParams;
use substrate::pdk::mos::{MosParams, LayoutMosParams, GateContactStrategy};
use substrate::layout::elements::mos::LayoutMos;
use substrate::schematic::circuit::Direction;
use substrate::schematic::context::SchematicCtx;
use substrate::schematic::elements::mos::SchematicMos;
use substrate::schematic::netlist::impls::spice::SpiceNetlister;

struct CometInv {
    params: PrimitiveGateParams,
}

impl Component for CometInv {
    type Params = PrimitiveGateParams;

    fn new(params: &Self::Params, _: &SubstrateCtx) -> Result<Self> {
        Ok(Self { params: *params })
    }

    fn name(&self) -> ArcStr {
        "comet_inv_r0".into()
    }

    fn schematic(&self, ctx: &mut SchematicCtx) -> Result<()> {
        let vdd = ctx.port("vdd", Direction::InOut);
        let vss = ctx.port("vss", Direction::InOut);
        let a = ctx.port("a", Direction::Input);
        let y = ctx.port("y", Direction::Output);

        let db = ctx.mos_db();
        let pmos_id = db.default_pmos().unwrap().id();
        let nmos_id = db.default_nmos().unwrap().id();

        let mut mp = ctx.instantiate::<SchematicMos>(&MosParams {
            w: self.params.pwidth, l: self.params.length,
            m: 1, nf: 1, id: pmos_id,
        })?;
        mp.connect_all([("d", &y), ("g", &a), ("s", &vdd), ("b", &vdd)]);
        mp.set_name("MP0");
        ctx.add_instance(mp);

        let mut mn = ctx.instantiate::<SchematicMos>(&MosParams {
            w: self.params.nwidth, l: self.params.length,
            m: 1, nf: 1, id: nmos_id,
        })?;
        mn.connect_all([("d", &y), ("g", &a), ("s", &vss), ("b", &vss)]);
        mn.set_name("MN0");
        ctx.add_instance(mn);
        Ok(())
    }

    fn layout(&self, ctx: &mut LayoutCtx) -> Result<()> {
        let inv = ctx.instantiate::<Inv>(&self.params)?;
        let layers = ctx.layers();
        let li = layers.get(Selector::Name("li1"))?;
        let tap = layers.get(Selector::Name("tap"))?;
        let psdm = layers.get(Selector::Name("psdm"))?;
        let nsdm = layers.get(Selector::Name("nsdm"))?;
        let nwell = layers.get(Selector::Name("nwell"))?;

        let pwell_existing = inv.layer_bbox(nwell).into_rect();
        ctx.draw_ref(&inv)?;
        for name in ["a", "y", "vdd", "vss"] {
            ctx.add_port(inv.port(name)?)?;
        }

        // One contacted tap below each source rail.
        // NMOS: p+ substrate tap to vss.
        // PMOS: n+ tap inside the extended nwell to vdd.
        for (name, implant, in_nwell) in [
            ("vss", psdm, false),
            ("vdd", nsdm, true),
        ] {
            let source = inv.port(name)?.largest_rect(li)?;
            let x = (source.p0.x + source.p1.x) / 2;
            let y = source.p0.y - 1_200;
            let region = Rect::new(
                Point::new(x - 250, y - 250),
                Point::new(x + 250, y + 250),
            );
            let vp = ViaParams::builder()
                .layers(tap, li)
                .geometry(region, region)
                .expand(ViaExpansion::Minimum)
                .build();
            let contact = ctx.instantiate::<Via>(&vp)?;
            let contact_tap = contact.layer_bbox(tap).into_rect();
            let tap_box = Rect::new(
                Point::new(
                    contact_tap.p0.x.min(region.p0.x),
                    contact_tap.p0.y.min(region.p0.y),
                ),
                Point::new(
                    contact_tap.p1.x.max(region.p1.x),
                    contact_tap.p1.y.max(region.p1.y),
                ),
            );
            ctx.draw_rect(tap, tap_box);
            let li_box = contact.layer_bbox(li).into_rect();

            ctx.draw_rect(implant, tap_box.expand(130));
            if in_nwell {
                let extended = Rect::new(
                    Point::new(
                        pwell_existing.p0.x.min(tap_box.p0.x - 400),
                        pwell_existing.p0.y.min(tap_box.p0.y - 400),
                    ),
                    Point::new(
                        pwell_existing.p1.x.max(tap_box.p1.x + 400),
                        pwell_existing.p1.y.max(tap_box.p1.y + 400),
                    ),
                );
                ctx.draw_rect(nwell, extended);
            }
            ctx.draw(contact)?;

            let rail = Rect::new(
                Point::new(
                    source.p0.x.min(li_box.p0.x),
                    source.p0.y.min(li_box.p0.y),
                ),
                Point::new(
                    source.p1.x.max(li_box.p1.x),
                    source.p1.y.max(li_box.p1.y),
                ),
            );
            ctx.draw_rect(li, rail);
        }
        ctx.flatten();
        Ok(())
    }
}


#[derive(Clone, Copy)]
struct BitcellParams {
    inverter: PrimitiveGateParams,
    access_width: i64,
}

struct CometBitcell {
    params: BitcellParams,
}

impl Component for CometBitcell {
    type Params = (PrimitiveGateParams, i64);

    fn new(params: &Self::Params, _: &SubstrateCtx) -> Result<Self> {
        Ok(Self {
            params: BitcellParams {
                inverter: params.0,
                access_width: params.1,
            },
        })
    }

    fn name(&self) -> ArcStr {
        "comet_bitcell_6t".into()
    }

    fn schematic(&self, ctx: &mut SchematicCtx) -> Result<()> {
        let vdd = ctx.port("vdd", Direction::InOut);
        let vss = ctx.port("vss", Direction::InOut);
        let bl = ctx.port("bl", Direction::InOut);
        let br = ctx.port("br", Direction::InOut);
        let wl = ctx.port("wl", Direction::Input);
        let q = ctx.signal("q");
        let qb = ctx.signal("qb");

        let mut left = ctx.instantiate::<CometInv>(&self.params.inverter)?;
        left.connect_all([
            ("vdd", &vdd), ("vss", &vss), ("a", &qb), ("y", &q),
        ]);
        left.set_name("IL");
        ctx.add_instance(left);

        let mut right = ctx.instantiate::<CometInv>(&self.params.inverter)?;
        right.connect_all([
            ("vdd", &vdd), ("vss", &vss), ("a", &q), ("y", &qb),
        ]);
        right.set_name("IR");
        ctx.add_instance(right);

        let nmos_id = ctx.mos_db().default_nmos().unwrap().id();
        for (name, bitline, storage) in [
            ("AXL", &bl, &q),
            ("AXR", &br, &qb),
        ] {
            let mut access = ctx.instantiate::<SchematicMos>(&MosParams {
                w: self.params.access_width,
                l: self.params.inverter.length,
                m: 1,
                nf: 1,
                id: nmos_id,
            })?;
            access.connect_all([
                ("d", bitline), ("g", &wl), ("s", storage), ("b", &vss),
            ]);
            access.set_name(name);
            ctx.add_instance(access);
        }
        Ok(())
    }


    fn layout(&self, ctx: &mut LayoutCtx) -> Result<()> {
        let li = ctx.layers().get(Selector::Name("li1"))?;
        let m1 = ctx.layers().get(Selector::Metal(1))?;
        let m2 = ctx.layers().get(Selector::Metal(2))?;

        let mut left = ctx.instantiate::<CometInv>(&self.params.inverter)?;
        let mut right = ctx.instantiate::<CometInv>(&self.params.inverter)?;
        left.set_loc(Point::new(0, 0));
        right.set_loc(Point::new(12_000, 0));
        ctx.draw_ref(&left)?;
        ctx.draw_ref(&right)?;

        let id = ctx.mos_db().default_nmos().unwrap().id();
        let params = LayoutMosParams {
            devices: vec![MosParams {
                w: self.params.access_width,
                l: self.params.inverter.length,
                m: 1, nf: 1, id,
            }],
            skip_sd_metal: vec![vec![]],
            deep_nwell: false,
            contact_strategy: GateContactStrategy::SingleSide,
        };
        let mut axl = ctx.instantiate::<LayoutMos>(&params)?;
        let mut axr = ctx.instantiate::<LayoutMos>(&params)?;
        axl.set_loc(Point::new(0, -8_000));
        axr.set_loc(Point::new(12_000, -8_000));
        ctx.draw_ref(&axl)?;
        ctx.draw_ref(&axr)?;

        // Source/drain assignment is interchangeable for these ordinary NMOS.
        // Each tuple is (LI contact center x, LI contact center y).
        let nets: Vec<(&str, i64, bool, Vec<(i64, i64)>)> = vec![
            ("q",   6_000, false, vec![(1800,555), (11645,340), (565,-7445)]),
            ("qb",  7_200, false, vec![(13800,555), (-355,340), (12565,-7445)]),
            ("vdd", 8_400, true,  vec![(3320,125), (15320,125)]),
            ("vss", 9_600, true,  vec![(1200,125), (13200,125)]),
            ("wl", -10_000, true, vec![(-1500,-7660), (10500,-7660)]),
            ("bl", -12_000, true, vec![(235,-7875)]),
            ("br", -13_200, true, vec![(12235,-7875)]),
        ];

        // Extend access gate LI leftward to avoid sharing a vertical track
        // with the inverter input above it.
        for offset in [0, 12_000] {
            ctx.draw_rect(li, Rect::new(
                Point::new(offset - 1585, -7745),
                Point::new(offset - 190, -7575),
            ));
        }

        for (name, track_y, external, points) in nets {
            let mut xmin = i64::MAX;
            let mut xmax = i64::MIN;
            for (x, y) in points {
                xmin = xmin.min(x);
                xmax = xmax.max(x);
                let landing = Rect::new(
                    Point::new(x-85, y-85),
                    Point::new(x+85, y+85),
                );
                let vp = ViaParams::builder()
                    .layers(li, m1)
                    .geometry(landing, landing)
                    .expand(ViaExpansion::Minimum)
                    .build();
                let via = ctx.instantiate::<Via>(&vp)?;
                ctx.draw(via)?;

                ctx.draw_rect(m1, Rect::new(
                    Point::new(x-85, y.min(track_y)-85),
                    Point::new(x+85, y.max(track_y)+85),
                ));
                let crossing = Rect::new(
                    Point::new(x-200, track_y-200),
                    Point::new(x+200, track_y+200),
                );
                let vp = ViaParams::builder()
                    .layers(m1, m2)
                    .geometry(crossing, crossing)
                    .expand(ViaExpansion::Minimum)
                    .build();
                let via = ctx.instantiate::<Via>(&vp)?;
                ctx.draw(via)?;
            }
            let wire = Rect::new(
                Point::new(xmin-300, track_y-200),
                Point::new(xmax+300, track_y+200),
            );
            ctx.draw_rect(m2, wire);
            if external {
                let mut port = CellPort::new(name);
                port.add(m2, subgeom::Shape::Rect(wire));
                ctx.add_port(port)?;
            }
            println!("ROUTED_NET={name} METAL2_Y_NM={track_y}");
        }
        ctx.flatten();
        Ok(())
    }
}


struct CometArray2x1 {
    params: (PrimitiveGateParams, i64),
}

impl Component for CometArray2x1 {
    type Params = (PrimitiveGateParams, i64);

    fn new(params: &Self::Params, _: &SubstrateCtx) -> Result<Self> {
        Ok(Self { params: *params })
    }

    fn name(&self) -> ArcStr {
        "comet_array_2x1".into()
    }

    fn schematic(&self, ctx: &mut SchematicCtx) -> Result<()> {
        let vdd = ctx.port("vdd", Direction::InOut);
        let vss = ctx.port("vss", Direction::InOut);
        let bl = ctx.port("bl", Direction::InOut);
        let br = ctx.port("br", Direction::InOut);
        let wl0 = ctx.port("wl0", Direction::Input);
        let wl1 = ctx.port("wl1", Direction::Input);

        for (name, wl) in [("ROW0", &wl0), ("ROW1", &wl1)] {
            let mut cell = ctx.instantiate::<CometBitcell>(&self.params)?;
            cell.connect_all([
                ("vdd", &vdd), ("vss", &vss),
                ("bl", &bl), ("br", &br), ("wl", wl),
            ]);
            cell.set_name(name);
            ctx.add_instance(cell);
        }
        Ok(())
    }

    fn layout(&self, ctx: &mut LayoutCtx) -> Result<()> {
        let m2 = ctx.layers().get(Selector::Metal(2))?;
        let m3 = ctx.layers().get(Selector::Metal(3))?;

        let mut row0 = ctx.instantiate::<CometBitcell>(&self.params)?;
        let mut row1 = ctx.instantiate::<CometBitcell>(&self.params)?;
        row0.set_loc(Point::new(0, 0));
        row1.set_loc(Point::new(0, 40_000));
        ctx.draw_ref(&row0)?;
        ctx.draw_ref(&row1)?;

        for (name, row) in [("wl0", &row0), ("wl1", &row1)] {
            let r = row.port("wl")?.largest_rect(m2)?;
            let mut port = CellPort::new(name);
            port.add(m2, subgeom::Shape::Rect(r));
            ctx.add_port(port)?;
            println!("ARRAY_PORT={name} RECT={r:?}");
        }

        for (name, bus_x) in [
            ("vdd", -5_000),
            ("vss", -7_000),
            ("bl", -9_000),
            ("br", -11_000),
        ] {
            let mut ymin = i64::MAX;
            let mut ymax = i64::MIN;
            for row in [&row0, &row1] {
                let source = row.port(name)?.largest_rect(m2)?;
                // Snap the contact center to the 5nm layout grid.
                let x = ((source.p0.x + source.p1.x) / 2).div_euclid(5) * 5;
                let y = ((source.p0.y + source.p1.y) / 2).div_euclid(5) * 5;
                ymin = ymin.min(y);
                ymax = ymax.max(y);

                let landing = Rect::new(
                    Point::new(x-200, y-200),
                    Point::new(x+200, y+200),
                );
                let vp = ViaParams::builder()
                    .layers(m2, m3)
                    .geometry(landing, landing)
                    .expand(ViaExpansion::Minimum)
                    .build();
                let via = ctx.instantiate::<Via>(&vp)?;
                ctx.draw(via)?;

                ctx.draw_rect(m3, Rect::new(
                    Point::new(bus_x.min(x)-400, y-400),
                    Point::new(bus_x.max(x)+400, y+400),
                ));
                let crossing = Rect::new(
                    Point::new(bus_x-400, y-400),
                    Point::new(bus_x+400, y+400),
                );
                let vp = ViaParams::builder()
                    .layers(m2, m3)
                    .geometry(crossing, crossing)
                    .expand(ViaExpansion::Minimum)
                    .build();
                let via = ctx.instantiate::<Via>(&vp)?;
                ctx.draw(via)?;
            }

            let bus = Rect::new(
                Point::new(bus_x-500, ymin-500),
                Point::new(bus_x+500, ymax+500),
            );
            ctx.draw_rect(m2, bus);
            let mut port = CellPort::new(name);
            port.add(m2, subgeom::Shape::Rect(bus));
            ctx.add_port(port)?;
            println!("ARRAY_SHARED_NET={name} METAL2_X_NM={bus_x}");
        }
        ctx.flatten();
        Ok(())
    }

}


struct CometNand2 {
    params: PrimitiveGateParams,
}

impl Component for CometNand2 {
    type Params = PrimitiveGateParams;

    fn new(params: &Self::Params, _: &SubstrateCtx) -> Result<Self> {
        Ok(Self { params: *params })
    }

    fn name(&self) -> ArcStr {
        "comet_nand2".into()
    }

    fn schematic(&self, ctx: &mut SchematicCtx) -> Result<()> {
        let vdd = ctx.port("vdd", Direction::InOut);
        let vss = ctx.port("vss", Direction::InOut);
        let a = ctx.port("a", Direction::Input);
        let b = ctx.port("b", Direction::Input);
        let y = ctx.port("y", Direction::Output);
        let mid = ctx.signal("mid");

        let db = ctx.mos_db();
        let pid = db.default_pmos().unwrap().id();
        let nid = db.default_nmos().unwrap().id();

        for (name, is_p, d, g, s) in [
            ("PA", true,  &y,   &a, &vdd),
            ("PB", true,  &y,   &b, &vdd),
            ("NA", false, &y,   &a, &mid),
            ("NB", false, &mid, &b, &vss),
        ] {
            let mut mos = ctx.instantiate::<SchematicMos>(&MosParams {
                w: if is_p {
                    self.params.pwidth
                } else {
                    2 * self.params.nwidth
                },
                l: self.params.length,
                m: 1, nf: 1,
                id: if is_p { pid } else { nid },
            })?;
            mos.connect_all([
                ("d", d), ("g", g), ("s", s),
                ("b", if is_p { &vdd } else { &vss }),
            ]);
            mos.set_name(name);
            ctx.add_instance(mos);
        }
        Ok(())
    }

    fn layout(&self, ctx: &mut LayoutCtx) -> Result<()> {
        let layout_params = PrimitiveGateParams {
            pwidth: self.params.pwidth,
            nwidth: 2 * self.params.nwidth,
            length: self.params.length,
        };
        let inv = ctx.instantiate::<Nand2>(&layout_params)?;
        let layers = ctx.layers();
        let li = layers.get(Selector::Name("li1"))?;
        let tap = layers.get(Selector::Name("tap"))?;
        let psdm = layers.get(Selector::Name("psdm"))?;
        let nsdm = layers.get(Selector::Name("nsdm"))?;
        let nwell = layers.get(Selector::Name("nwell"))?;

        let pwell_existing = inv.layer_bbox(nwell).into_rect();
        ctx.draw_ref(&inv)?;
        for (physical, logical) in [
            ("b", "a"), ("a", "b"), ("y", "y"),
            ("vdd", "vdd"), ("vss", "vss"),
        ] {
            ctx.add_port(inv.port(physical)?.into_cell_port().named(logical))?;
        }

        // One contacted tap below each source rail.
        // NMOS: p+ substrate tap to vss.
        // PMOS: n+ tap inside the extended nwell to vdd.
        for (name, implant, in_nwell) in [
            ("vss", psdm, false),
            ("vdd", nsdm, true),
        ] {
            let source = inv.port(name)?.largest_rect(li)?;
            let x = if in_nwell {
                pwell_existing.p1.x + 2_000
            } else {
                (source.p0.x + source.p1.x) / 2
            };
            let y = if in_nwell {
                (source.p0.y + source.p1.y) / 2
            } else {
                source.p0.y - 1_200
            };
            let region = Rect::new(
                Point::new(x - 250, y - 250),
                Point::new(x + 250, y + 250),
            );
            let vp = ViaParams::builder()
                .layers(tap, li)
                .geometry(region, region)
                .expand(ViaExpansion::Minimum)
                .build();
            let contact = ctx.instantiate::<Via>(&vp)?;
            let contact_tap = contact.layer_bbox(tap).into_rect();
            let tap_box = Rect::new(
                Point::new(
                    contact_tap.p0.x.min(region.p0.x),
                    contact_tap.p0.y.min(region.p0.y),
                ),
                Point::new(
                    contact_tap.p1.x.max(region.p1.x),
                    contact_tap.p1.y.max(region.p1.y),
                ),
            );
            ctx.draw_rect(tap, tap_box);
            let li_box = contact.layer_bbox(li).into_rect();

            ctx.draw_rect(implant, tap_box.expand(130));
            if in_nwell {
                let extended = Rect::new(
                    Point::new(
                        pwell_existing.p0.x.min(tap_box.p0.x - 400),
                        pwell_existing.p0.y.min(tap_box.p0.y - 400),
                    ),
                    Point::new(
                        pwell_existing.p1.x.max(tap_box.p1.x + 400),
                        pwell_existing.p1.y.max(tap_box.p1.y + 400),
                    ),
                );
                ctx.draw_rect(nwell, extended);
            }
            ctx.draw(contact)?;

            let rail = Rect::new(
                Point::new(
                    source.p0.x.min(li_box.p0.x),
                    source.p0.y.min(li_box.p0.y),
                ),
                Point::new(
                    source.p1.x.max(li_box.p1.x),
                    source.p1.y.max(li_box.p1.y),
                ),
            );
            if in_nwell {
                // Keep the VDD connection at the middle source height.
                // Do not extend LI vertically across the output terminals.
                let rail = Rect::new(
                    Point::new(source.p0.x, source.p0.y),
                    Point::new(li_box.p1.x, source.p1.y),
                );
                ctx.draw_rect(li, rail);
            } else {
                ctx.draw_rect(li, rail);
            }
        }
        ctx.flatten();
        Ok(())
    }
}

struct CometRowDecoder {
    params: PrimitiveGateParams,
}

impl Component for CometRowDecoder {
    type Params = PrimitiveGateParams;

    fn new(params: &Self::Params, _: &SubstrateCtx) -> Result<Self> {
        Ok(Self { params: *params })
    }

    fn name(&self) -> ArcStr {
        "comet_row_decoder_1to2".into()
    }

    fn schematic(&self, ctx: &mut SchematicCtx) -> Result<()> {
        let vdd = ctx.port("vdd", Direction::InOut);
        let vss = ctx.port("vss", Direction::InOut);
        let a = ctx.port("a", Direction::Input);
        let en = ctx.port("en", Direction::Input);
        let wl0 = ctx.port("wl0", Direction::Output);
        let wl1 = ctx.port("wl1", Direction::Output);
        let ab = ctx.signal("ab");
        let n0 = ctx.signal("n0");
        let n1 = ctx.signal("n1");

        let mut inv = ctx.instantiate::<CometInv>(&self.params)?;
        inv.connect_all([
            ("vdd", &vdd), ("vss", &vss), ("a", &a), ("y", &ab),
        ]);
        inv.set_name("ADDR_INV");
        ctx.add_instance(inv);

        for (name, address, intermediate, output) in [
            ("ROW0", &ab, &n0, &wl0),
            ("ROW1", &a,  &n1, &wl1),
        ] {
            let mut nand = ctx.instantiate::<CometNand2>(&self.params)?;
            nand.connect_all([
                ("vdd", &vdd), ("vss", &vss),
                ("a", &en), ("b", address), ("y", intermediate),
            ]);
            nand.set_name(format!("{name}_NAND"));
            ctx.add_instance(nand);

            let mut driver = ctx.instantiate::<CometInv>(&self.params)?;
            driver.connect_all([
                ("vdd", &vdd), ("vss", &vss),
                ("a", intermediate), ("y", output),
            ]);
            driver.set_name(format!("{name}_DRIVER"));
            ctx.add_instance(driver);
        }
        Ok(())
    }

    fn layout(&self, ctx: &mut LayoutCtx) -> Result<()> {
        let li = ctx.layers().get(Selector::Name("li1"))?;
        let m1 = ctx.layers().get(Selector::Metal(1))?;
        let m2 = ctx.layers().get(Selector::Metal(2))?;

        let mut addr = ctx.instantiate::<CometInv>(&self.params)?;
        let mut nand0 = ctx.instantiate::<CometNand2>(&self.params)?;
        let mut driver0 = ctx.instantiate::<CometInv>(&self.params)?;
        let mut nand1 = ctx.instantiate::<CometNand2>(&self.params)?;
        let mut driver1 = ctx.instantiate::<CometInv>(&self.params)?;

        addr.set_loc(Point::new(0, 0));
        nand0.set_loc(Point::new(16_000, 0));
        driver0.set_loc(Point::new(32_000, 0));
        nand1.set_loc(Point::new(48_000, 0));
        driver1.set_loc(Point::new(64_000, 0));

        ctx.draw_ref(&addr)?;
        ctx.draw_ref(&nand0)?;
        ctx.draw_ref(&driver0)?;
        ctx.draw_ref(&nand1)?;
        ctx.draw_ref(&driver1)?;

        // Collect LI terminals before constructing the upper-metal wiring.
        let mut terminals: Vec<(&str, i64, i64)> = Vec::new();
        for (label, gate, mapping) in [
            ("ADDR", &addr, vec![
                ("a","a"), ("y","ab"), ("vdd","vdd"), ("vss","vss")]),
            ("NAND0", &nand0, vec![
                ("a","en"), ("b","ab"), ("y","n0"),
                ("vdd","vdd"), ("vss","vss")]),
            ("DRIVER0", &driver0, vec![
                ("a","n0"), ("y","wl0"), ("vdd","vdd"), ("vss","vss")]),
            ("NAND1", &nand1, vec![
                ("a","en"), ("b","a"), ("y","n1"),
                ("vdd","vdd"), ("vss","vss")]),
            ("DRIVER1", &driver1, vec![
                ("a","n1"), ("y","wl1"), ("vdd","vdd"), ("vss","vss")]),
        ] {
            for (pin, net) in mapping {
                let r = gate.port(pin)?.largest_rect(li)?;
                let mut x = ((r.p0.x + r.p1.x) / 2).div_euclid(5) * 5;
                let y = ((r.p0.y + r.p1.y) / 2).div_euclid(5) * 5;

                if pin == "b" {
                    // NAND inputs share an x position. Give input b
                    // its own vertical track using a short LI extension.
                    x = (r.p0.x - 1_200).div_euclid(5) * 5;
                    ctx.draw_rect(li, Rect::new(
                        Point::new(x - 85, y - 85),
                        Point::new(r.p1.x, y + 85),
                    ));
                } else if pin == "y" {
                    // Contact the NMOS side of the output, away from VDD.
                    x = (r.p0.x + 300).div_euclid(5) * 5;
                } else if pin == "vss" {
                    x = (r.p1.x - 300).div_euclid(5) * 5;
                }

                println!("DECODER_TERMINAL={label}.{pin} NET={net} X={x} Y={y}");
                terminals.push((net, x, y));
            }
        }

        for (name, track_y, external) in [
            ("a",    6_000_i64, true),
            ("ab",   7_600, false),
            ("en",   9_200, true),
            ("n0",  10_800, false),
            ("n1",  12_400, false),
            ("wl0", 14_000, true),
            ("wl1", 15_600, true),
            ("vdd", 17_200, true),
            ("vss", 18_800, true),
        ] {
            let mut xmin = i64::MAX;
            let mut xmax = i64::MIN;
            for &(net, x, y) in &terminals {
                if net != name {
                    continue;
                }
                xmin = xmin.min(x);
                xmax = xmax.max(x);
                let landing = Rect::new(
                    Point::new(x-85, y-85),
                    Point::new(x+85, y+85),
                );
                let vp = ViaParams::builder()
                    .layers(li, m1)
                    .geometry(landing, landing)
                    .expand(ViaExpansion::Minimum)
                    .build();
                let via = ctx.instantiate::<Via>(&vp)?;
                ctx.draw(via)?;

                ctx.draw_rect(m1, Rect::new(
                    Point::new(x-85, y.min(track_y)-85),
                    Point::new(x+85, y.max(track_y)+85),
                ));
                let crossing = Rect::new(
                    Point::new(x-200, track_y-200),
                    Point::new(x+200, track_y+200),
                );
                let vp = ViaParams::builder()
                    .layers(m1, m2)
                    .geometry(crossing, crossing)
                    .expand(ViaExpansion::Minimum)
                    .build();
                let via = ctx.instantiate::<Via>(&vp)?;
                ctx.draw(via)?;
            }
            let wire = Rect::new(
                Point::new(xmin-300, track_y-200),
                Point::new(xmax+300, track_y+200),
            );
            ctx.draw_rect(m2, wire);
            if external {
                let mut port = CellPort::new(name);
                port.add(m2, subgeom::Shape::Rect(wire));
                ctx.add_port(port)?;
            }
            println!("DECODER_ROUTED_NET={name} METAL2_Y_NM={track_y}");
        }

        ctx.flatten();
        Ok(())
    }
}


struct CometDecodedArray {
    params: (PrimitiveGateParams, i64),
}

impl Component for CometDecodedArray {
    type Params = (PrimitiveGateParams, i64);

    fn new(params: &Self::Params, _: &SubstrateCtx) -> Result<Self> {
        Ok(Self { params: *params })
    }

    fn name(&self) -> ArcStr {
        "comet_decoded_array_2x1".into()
    }

    fn schematic(&self, ctx: &mut SchematicCtx) -> Result<()> {
        let vdd = ctx.port("vdd", Direction::InOut);
        let vss = ctx.port("vss", Direction::InOut);
        let bl = ctx.port("bl", Direction::InOut);
        let br = ctx.port("br", Direction::InOut);
        let a = ctx.port("a", Direction::Input);
        let en = ctx.port("en", Direction::Input);
        let wl0 = ctx.signal("wl0");
        let wl1 = ctx.signal("wl1");

        let mut decoder = ctx.instantiate::<CometRowDecoder>(&self.params.0)?;
        decoder.connect_all([
            ("vdd", &vdd), ("vss", &vss), ("a", &a), ("en", &en),
            ("wl0", &wl0), ("wl1", &wl1),
        ]);
        decoder.set_name("DEC");
        ctx.add_instance(decoder);

        let mut array = ctx.instantiate::<CometArray2x1>(&self.params)?;
        array.connect_all([
            ("vdd", &vdd), ("vss", &vss), ("bl", &bl), ("br", &br),
            ("wl0", &wl0), ("wl1", &wl1),
        ]);
        array.set_name("ARRAY");
        ctx.add_instance(array);
        Ok(())
    }

    fn layout(&self, ctx: &mut LayoutCtx) -> Result<()> {
        let m2 = ctx.layers().get(Selector::Metal(2))?;
        let m3 = ctx.layers().get(Selector::Metal(3))?;

        let mut decoder = ctx.instantiate::<CometRowDecoder>(&self.params.0)?;
        let mut array = ctx.instantiate::<CometArray2x1>(&self.params)?;
        decoder.set_loc(Point::new(0, 0));
        array.set_loc(Point::new(100_000, 40_000));
        ctx.draw_ref(&decoder)?;
        ctx.draw_ref(&array)?;

        for name in ["a", "en"] {
            let r = decoder.port(name)?.largest_rect(m2)?;
            let mut port = CellPort::new(name);
            port.add(m2, subgeom::Shape::Rect(r));
            ctx.add_port(port)?;
        }
        for name in ["bl", "br"] {
            let r = array.port(name)?.largest_rect(m2)?;
            let mut port = CellPort::new(name);
            port.add(m2, subgeom::Shape::Rect(r));
            ctx.add_port(port)?;
        }

        for (name, bus_x, external) in [
            ("wl0", 80_000_i64, false),
            ("wl1", 82_000, false),
            ("vdd", 84_000, true),
            ("vss", 86_000, true),
        ] {
            let dec = decoder.port(name)?.largest_rect(m2)?;
            let arr = array.port(name)?.largest_rect(m2)?;
            let dx = ((dec.p0.x + dec.p1.x) / 2).div_euclid(5) * 5;
            let dy = ((dec.p0.y + dec.p1.y) / 2).div_euclid(5) * 5;
            let ax = ((arr.p0.x + arr.p1.x) / 2).div_euclid(5) * 5;

            // Power connects at the lower row's existing power branch,
            // avoiding unrelated horizontal array wiring.
            let ay = if external {
                (arr.p0.y + 500).div_euclid(5) * 5
            } else {
                ((arr.p0.y + arr.p1.y) / 2).div_euclid(5) * 5
            };

            for (x, y) in [(dx, dy), (ax, ay)] {
                let landing = Rect::new(
                    Point::new(x-200, y-200),
                    Point::new(x+200, y+200),
                );
                let vp = ViaParams::builder()
                    .layers(m2, m3)
                    .geometry(landing, landing)
                    .expand(ViaExpansion::Minimum)
                    .build();
                let via = ctx.instantiate::<Via>(&vp)?;
                ctx.draw(via)?;

                ctx.draw_rect(m3, Rect::new(
                    Point::new(x.min(bus_x)-400, y-400),
                    Point::new(x.max(bus_x)+400, y+400),
                ));

                let crossing = Rect::new(
                    Point::new(bus_x-400, y-400),
                    Point::new(bus_x+400, y+400),
                );
                let vp = ViaParams::builder()
                    .layers(m2, m3)
                    .geometry(crossing, crossing)
                    .expand(ViaExpansion::Minimum)
                    .build();
                let via = ctx.instantiate::<Via>(&vp)?;
                ctx.draw(via)?;
            }

            let bus = Rect::new(
                Point::new(bus_x-500, dy.min(ay)-500),
                Point::new(bus_x+500, dy.max(ay)+500),
            );
            ctx.draw_rect(m2, bus);
            if external {
                let mut port = CellPort::new(name);
                port.add(m2, subgeom::Shape::Rect(bus));
                ctx.add_port(port)?;
            }
            println!(
                "INTEGRATED_NET={name} BUS_X={bus_x} DEC=({dx},{dy}) ARRAY=({ax},{ay})"
            );
        }

        ctx.flatten();
        Ok(())
    }
}


struct CometPrecharge {
    params: PrimitiveGateParams,
}

impl Component for CometPrecharge {
    type Params = PrimitiveGateParams;

    fn new(params: &Self::Params, _: &SubstrateCtx) -> Result<Self> {
        Ok(Self { params: *params })
    }

    fn name(&self) -> ArcStr {
        "comet_precharge_3p".into()
    }

    fn schematic(&self, ctx: &mut SchematicCtx) -> Result<()> {
        let vdd = ctx.port("vdd", Direction::InOut);
        let bl = ctx.port("bl", Direction::InOut);
        let br = ctx.port("br", Direction::InOut);
        let pc_b = ctx.port("pc_b", Direction::Input);
        let pmos_id = ctx.mos_db().default_pmos().unwrap().id();

        for (name, drain, source) in [
            ("PBL", &bl, &vdd),
            ("PBR", &br, &vdd),
            ("PEQ", &bl, &br),
        ] {
            let mut mos = ctx.instantiate::<SchematicMos>(&MosParams {
                w: self.params.pwidth,
                l: self.params.length,
                m: 1,
                nf: 1,
                id: pmos_id,
            })?;
            mos.connect_all([
                ("d", drain), ("g", &pc_b),
                ("s", source), ("b", &vdd),
            ]);
            mos.set_name(name);
            ctx.add_instance(mos);
        }
        Ok(())
    }


    fn layout(&self, ctx: &mut LayoutCtx) -> Result<()> {
        let li = ctx.layers().get(Selector::Name("li1"))?;
        let tap = ctx.layers().get(Selector::Name("tap"))?;
        let nsdm = ctx.layers().get(Selector::Name("nsdm"))?;
        let nwell = ctx.layers().get(Selector::Name("nwell"))?;
        let m1 = ctx.layers().get(Selector::Metal(1))?;
        let m2 = ctx.layers().get(Selector::Metal(2))?;
        let id = ctx.mos_db().default_pmos().unwrap().id();

        let params = LayoutMosParams {
            devices: vec![MosParams {
                w: self.params.pwidth,
                l: self.params.length,
                m: 1, nf: 1, id,
            }],
            skip_sd_metal: vec![vec![]],
            deep_nwell: false,
            contact_strategy: GateContactStrategy::SingleSide,
        };

        let mut terminals: Vec<(&str, i64, i64)> = Vec::new();
        for (name, offset, lower_net, upper_net) in [
            ("PBL", 0_i64, "vdd", "bl"),
            ("PBR", 8_000, "vdd", "br"),
            ("PEQ", 16_000, "bl", "br"),
        ] {
            let mut mos = ctx.instantiate::<LayoutMos>(&params)?;
            mos.set_loc(Point::new(offset, 0));
            let well = mos.layer_bbox(nwell).into_rect();
            ctx.draw_ref(&mos)?;

            // Separated contact tracks within the existing LI terminals.
            for (pin, net, x) in [
                ("gate_0", "pc_b", offset-355),
                ("sd_0_0", lower_net, offset+150),
                ("sd_0_1", upper_net, offset+650),
            ] {
                let r = mos.port(pin)?.largest_rect(li)?;
                let y = ((r.p0.y+r.p1.y)/2).div_euclid(5)*5;
                terminals.push((net, x, y));
                println!("PRECHARGE_TERMINAL={name}.{pin} NET={net} X={x} Y={y}");
            }

            // An independent contacted n+ well tap for each PMOS.
            let tx = offset+2_000;
            let ty = -1_400;
            let region = Rect::new(
                Point::new(tx-250, ty-250),
                Point::new(tx+250, ty+250),
            );
            let vp = ViaParams::builder()
                .layers(tap, li)
                .geometry(region, region)
                .expand(ViaExpansion::Minimum)
                .build();
            let contact = ctx.instantiate::<Via>(&vp)?;
            let ct = contact.layer_bbox(tap).into_rect();
            let cl = contact.layer_bbox(li).into_rect();
            let tap_box = Rect::new(
                Point::new(ct.p0.x.min(region.p0.x), ct.p0.y.min(region.p0.y)),
                Point::new(ct.p1.x.max(region.p1.x), ct.p1.y.max(region.p1.y)),
            );
            ctx.draw_rect(tap, tap_box);
            ctx.draw_rect(nsdm, tap_box.expand(130));
            ctx.draw_rect(nwell, Rect::new(
                Point::new(
                    well.p0.x.min(tap_box.p0.x-400),
                    well.p0.y.min(tap_box.p0.y-400),
                ),
                Point::new(
                    well.p1.x.max(tap_box.p1.x+400),
                    well.p1.y.max(tap_box.p1.y+400),
                ),
            ));
            let x = ((cl.p0.x+cl.p1.x)/2).div_euclid(5)*5;
            let y = ((cl.p0.y+cl.p1.y)/2).div_euclid(5)*5;
            terminals.push(("vdd", x, y));
            ctx.draw(contact)?;
            println!("PRECHARGE_WELL_TAP={name} X={x} Y={y}");
        }

        for (name, track_y) in [
            ("vdd", -4_000_i64),
            ("bl", 6_000),
            ("br", 7_600),
            ("pc_b", 9_200),
        ] {
            let mut xmin = i64::MAX;
            let mut xmax = i64::MIN;
            for &(net, x, y) in &terminals {
                if net != name { continue; }
                xmin = xmin.min(x);
                xmax = xmax.max(x);

                let landing = Rect::new(
                    Point::new(x-85, y-85),
                    Point::new(x+85, y+85),
                );
                let vp = ViaParams::builder()
                    .layers(li, m1)
                    .geometry(landing, landing)
                    .expand(ViaExpansion::Minimum)
                    .build();
                let via = ctx.instantiate::<Via>(&vp)?;
                ctx.draw(via)?;

                ctx.draw_rect(m1, Rect::new(
                    Point::new(x-85, y.min(track_y)-85),
                    Point::new(x+85, y.max(track_y)+85),
                ));
                let crossing = Rect::new(
                    Point::new(x-200, track_y-200),
                    Point::new(x+200, track_y+200),
                );
                let vp = ViaParams::builder()
                    .layers(m1, m2)
                    .geometry(crossing, crossing)
                    .expand(ViaExpansion::Minimum)
                    .build();
                let via = ctx.instantiate::<Via>(&vp)?;
                ctx.draw(via)?;
            }
            let wire = Rect::new(
                Point::new(xmin-300, track_y-200),
                Point::new(xmax+300, track_y+200),
            );
            ctx.draw_rect(m2, wire);
            let mut port = CellPort::new(name);
            port.add(m2, subgeom::Shape::Rect(wire));
            ctx.add_port(port)?;
            println!("PRECHARGE_ROUTED_NET={name} METAL2_Y_NM={track_y}");
        }

        ctx.flatten();
        Ok(())
    }
}


struct CometWriteDriver {
    params: PrimitiveGateParams,
}

impl Component for CometWriteDriver {
    type Params = PrimitiveGateParams;

    fn new(params: &Self::Params, _: &SubstrateCtx) -> Result<Self> {
        Ok(Self { params: *params })
    }

    fn name(&self) -> ArcStr {
        "comet_write_driver".into()
    }

    fn schematic(&self, ctx: &mut SchematicCtx) -> Result<()> {
        let vdd = ctx.port("vdd", Direction::InOut);
        let vss = ctx.port("vss", Direction::InOut);
        let din = ctx.port("din", Direction::Input);
        let we = ctx.port("we", Direction::Input);
        let bl = ctx.port("bl", Direction::InOut);
        let br = ctx.port("br", Direction::InOut);
        let db = ctx.signal("db");
        let web = ctx.signal("web");

        for (name, input, output) in [
            ("DATA_INV", &din, &db),
            ("WE_INV", &we, &web),
        ] {
            let mut inv = ctx.instantiate::<CometInv>(&self.params)?;
            inv.connect_all([
                ("vdd", &vdd), ("vss", &vss),
                ("a", input), ("y", output),
            ]);
            inv.set_name(name);
            ctx.add_instance(inv);
        }

        let pid = ctx.mos_db().default_pmos().unwrap().id();
        let nid = ctx.mos_db().default_nmos().unwrap().id();

        for (name, data_gate, output) in [
            ("BL", &db, &bl),
            ("BR", &din, &br),
        ] {
            let p_mid = ctx.signal(format!("{name}_p_mid"));
            let n_mid = ctx.signal(format!("{name}_n_mid"));

            // Supply-side enable devices isolate both drive paths when WE=0.
            for (suffix, drain, gate, source, body, id, width) in [
                ("PEN", &p_mid, &web, &vdd, &vdd, pid, 2*self.params.pwidth),
                ("PDATA", output, data_gate, &p_mid, &vdd, pid, 2*self.params.pwidth),
                ("NDATA", output, data_gate, &n_mid, &vss, nid, 2*self.params.nwidth),
                ("NEN", &n_mid, &we, &vss, &vss, nid, 2*self.params.nwidth),
            ] {
                let mut mos = ctx.instantiate::<SchematicMos>(&MosParams {
                    w: width,
                    l: self.params.length,
                    m: 1, nf: 1, id,
                })?;
                mos.connect_all([
                    ("d", drain), ("g", gate),
                    ("s", source), ("b", body),
                ]);
                mos.set_name(format!("{name}_{suffix}"));
                ctx.add_instance(mos);
            }
        }
        Ok(())
    }

    fn layout(&self, ctx: &mut LayoutCtx) -> Result<()> {
        let li = ctx.layers().get(Selector::Name("li1"))?;
        
        let nwell = ctx.layers().get(Selector::Name("nwell"))?;
        let tap = ctx.layers().get(Selector::Name("tap"))?;
        let nsdm = ctx.layers().get(Selector::Name("nsdm"))?;
        let psdm = ctx.layers().get(Selector::Name("psdm"))?;
        let m1 = ctx.layers().get(Selector::Metal(1))?;
        let m2 = ctx.layers().get(Selector::Metal(2))?;
        let mut terminals: Vec<(&str,i64,i64)> = Vec::new();


        for (name, x) in [
            ("DATA_INV", 0_i64),
            ("WE_INV", 16_000),
        ] {
            let mut inv = ctx.instantiate::<CometInv>(&self.params)?;
            inv.set_loc(Point::new(x, 0));
            for pin in ["a", "y", "vdd", "vss"] {
                let r = inv.port(pin)?.largest_rect(li)?;
                println!("DRIVER_PORT={name}.{pin} LI_RECT={r:?}");
            }
            
            for pin in ["a","y","vdd","vss"] {
                let r=inv.port(pin)?.largest_rect(li)?;
                let net=match pin {
                    "a"=>if name=="DATA_INV" {"din"} else {"we"},
                    "y"=>if name=="DATA_INV" {"db"} else {"web"},
                    "vdd"=>"vdd",
                    _=>"vss",
                };
                let px=(match pin {
                    "y"=>r.p0.x+300,
                    "vss"=>r.p1.x-300,
                    _=>(r.p0.x+r.p1.x)/2,
                }).div_euclid(5)*5;
                let py=((r.p0.y+r.p1.y)/2).div_euclid(5)*5;
                terminals.push((net,px,py));
            }
            ctx.draw_ref(&inv)?;

        }

        let pid = ctx.mos_db().default_pmos().unwrap().id();
        let nid = ctx.mos_db().default_nmos().unwrap().id();
        for (index, (name, is_pmos)) in [
            ("BL_PEN", true),
            ("BL_PDATA", true),
            ("BL_NDATA", false),
            ("BL_NEN", false),
            ("BR_PEN", true),
            ("BR_PDATA", true),
            ("BR_NDATA", false),
            ("BR_NEN", false),
        ].into_iter().enumerate() {
            let params = LayoutMosParams {
                devices: vec![MosParams {
                    w: if is_pmos {
                        2*self.params.pwidth
                    } else {
                        2*self.params.nwidth
                    },
                    l: self.params.length,
                    m: 1,
                    nf: 1,
                    id: if is_pmos { pid } else { nid },
                }],
                skip_sd_metal: vec![vec![]],
                deep_nwell: false,
                contact_strategy: GateContactStrategy::SingleSide,
            };
            let x = 32_000 + 12_000*(index as i64);
            let mut mos = ctx.instantiate::<LayoutMos>(&params)?;
            mos.set_loc(Point::new(x, 0));

            if is_pmos {
                println!(
                    "DRIVER_DEVICE={name} NWELL_RECT={:?}",
                    mos.layer_bbox(nwell).into_rect()
                );
            }
            for pin in ["gate_0", "sd_0_0", "sd_0_1"] {
                let r = mos.port(pin)?.largest_rect(li)?;
                println!("DRIVER_PORT={name}.{pin} LI_RECT={r:?}");
            }
            
            let (gate,drain,source)=[
                ("web","BL_p_mid","vdd"),
                ("db","bl","BL_p_mid"),
                ("db","bl","BL_n_mid"),
                ("we","BL_n_mid","vss"),
                ("web","BR_p_mid","vdd"),
                ("din","br","BR_p_mid"),
                ("din","br","BR_n_mid"),
                ("we","BR_n_mid","vss"),
            ][index];
            for (pin,net) in [
                ("gate_0",gate),("sd_0_0",drain),("sd_0_1",source)
            ] {
                let r=mos.port(pin)?.largest_rect(li)?;
                let px=(match pin {
                    "sd_0_0"=>r.p0.x+200,
                    "sd_0_1"=>r.p1.x-200,
                    _=>(r.p0.x+r.p1.x)/2,
                }).div_euclid(5)*5;
                let py=((r.p0.y+r.p1.y)/2).div_euclid(5)*5;
                terminals.push((net,px,py));
            }

            let tx=x+6_000;
            let ty=-1_400;
            let region=Rect::new(
                Point::new(tx-250,ty-250),
                Point::new(tx+250,ty+250),
            );
            let vp=ViaParams::builder()
                .layers(tap,li).geometry(region,region)
                .expand(ViaExpansion::Minimum).build();
            let contact=ctx.instantiate::<Via>(&vp)?;
            let ct=contact.layer_bbox(tap).into_rect();
            let cl=contact.layer_bbox(li).into_rect();
            let tb=Rect::new(
                Point::new(ct.p0.x.min(region.p0.x),ct.p0.y.min(region.p0.y)),
                Point::new(ct.p1.x.max(region.p1.x),ct.p1.y.max(region.p1.y)),
            );
            ctx.draw_rect(tap,tb);
            ctx.draw_rect(if is_pmos {nsdm} else {psdm},tb.expand(130));
            if is_pmos {
                let well=mos.layer_bbox(nwell).into_rect();
                ctx.draw_rect(nwell,Rect::new(
                    Point::new(
                        well.p0.x.min(tb.p0.x-400),
                        well.p0.y.min(tb.p0.y-400),
                    ),
                    Point::new(
                        well.p1.x.max(tb.p1.x+400),
                        well.p1.y.max(tb.p1.y+400),
                    ),
                ));
            }
            let px=((cl.p0.x+cl.p1.x)/2).div_euclid(5)*5;
            let py=((cl.p0.y+cl.p1.y)/2).div_euclid(5)*5;
            terminals.push((if is_pmos {"vdd"} else {"vss"},px,py));
            ctx.draw(contact)?;
            ctx.draw_ref(&mos)?;

        }

        
        for (name,track_y,external) in [
            ("din",6_000_i64,true),("we",7_600,true),
            ("db",9_200,false),("web",10_800,false),
            ("BL_p_mid",12_400,false),("BL_n_mid",14_000,false),
            ("BR_p_mid",15_600,false),("BR_n_mid",17_200,false),
            ("bl",18_800,true),("br",20_400,true),
            ("vdd",22_000,true),("vss",23_600,true),
        ] {
            let mut xmin=i64::MAX;
            let mut xmax=i64::MIN;
            for &(net,x,y) in &terminals {
                if net!=name {continue;}
                xmin=xmin.min(x);
                xmax=xmax.max(x);
                let landing=Rect::new(
                    Point::new(x-85,y-85),Point::new(x+85,y+85),
                );
                let vp=ViaParams::builder()
                    .layers(li,m1).geometry(landing,landing)
                    .expand(ViaExpansion::Minimum).build();
                let via=ctx.instantiate::<Via>(&vp)?;
                ctx.draw(via)?;
                ctx.draw_rect(m1,Rect::new(
                    Point::new(x-85,y.min(track_y)-85),
                    Point::new(x+85,y.max(track_y)+85),
                ));
                let crossing=Rect::new(
                    Point::new(x-200,track_y-200),
                    Point::new(x+200,track_y+200),
                );
                let vp=ViaParams::builder()
                    .layers(m1,m2).geometry(crossing,crossing)
                    .expand(ViaExpansion::Minimum).build();
                let via=ctx.instantiate::<Via>(&vp)?;
                ctx.draw(via)?;
            }
            let wire=Rect::new(
                Point::new(xmin-300,track_y-200),
                Point::new(xmax+300,track_y+200),
            );
            ctx.draw_rect(m2,wire);
            if external {
                let mut port=CellPort::new(name);
                port.add(m2,subgeom::Shape::Rect(wire));
                ctx.add_port(port)?;
            }
            println!("DRIVER_ROUTED_NET={name} METAL2_Y_NM={track_y}");
        }
        ctx.flatten();

        Ok(())
    }
}


struct CometSenseAmp {
    params: PrimitiveGateParams,
}


impl Component for CometSenseAmp {
    type Params = PrimitiveGateParams;

    fn new(params: &Self::Params, _: &SubstrateCtx) -> Result<Self> {
        Ok(Self { params: *params })
    }

    fn name(&self) -> ArcStr {
        "comet_sense_amp".into()
    }

    fn schematic(&self, ctx: &mut SchematicCtx) -> Result<()> {
        let vdd=ctx.port("vdd",Direction::InOut);
        let vss=ctx.port("vss",Direction::InOut);
        let bl=ctx.port("bl",Direction::Input);
        let br=ctx.port("br",Direction::Input);
        let en=ctx.port("sa_en",Direction::Input);
        let dout=ctx.port("dout",Direction::Output);
        let mirror=ctx.signal("mirror");
        let sense=ctx.signal("sense");
        let tail=ctx.signal("tail");
        let raw=ctx.signal("raw");
        let buffered_b=ctx.signal("buffered_b");
        let pid=ctx.mos_db().default_pmos().unwrap().id();
        let nid=ctx.mos_db().default_nmos().unwrap().id();

        // Each entry explicitly records width and length in nanometers.
        for (name,d,g,s,b,id,w,l) in [
            ("NLEFT",&mirror,&br,&tail,&vss,nid,
                self.params.nwidth,self.params.length),
            ("NRIGHT",&sense,&bl,&tail,&vss,nid,
                self.params.nwidth,self.params.length),
            ("NTAIL",&tail,&en,&vss,&vss,nid,420,600),
            ("PMIRROR_REF",&mirror,&mirror,&vdd,&vdd,pid,
                self.params.pwidth,self.params.length),
            ("PMIRROR_OUT",&sense,&mirror,&vdd,&vdd,pid,
                self.params.pwidth,self.params.length),
            ("PRESET",&sense,&en,&vdd,&vdd,pid,
                self.params.pwidth,self.params.length),
        ] {
            let mut mos=ctx.instantiate::<SchematicMos>(&MosParams {
                w,l,m:1,nf:1,id,
            })?;
            mos.connect_all([("d",d),("g",g),("s",s),("b",b)]);
            mos.set_name(name);
            ctx.add_instance(mos);
        }

        // Original output inverter followed by two restoration inverters.
        for (name,input,output) in [
            ("OUTPUT_INV",&sense,&raw),
            ("RESTORE_INV0",&raw,&buffered_b),
            ("RESTORE_INV1",&buffered_b,&dout),
        ] {
            let mut inv=ctx.instantiate::<CometInv>(&self.params)?;
            inv.connect_all([
                ("vdd",&vdd),("vss",&vss),
                ("a",input),("y",output),
            ]);
            inv.set_name(name);
            ctx.add_instance(inv);
        }
        Ok(())
    }

    fn layout(&self, ctx: &mut LayoutCtx) -> Result<()> {
        let li=ctx.layers().get(Selector::Name("li1"))?;
        
        let nwell=ctx.layers().get(Selector::Name("nwell"))?;
        let tap=ctx.layers().get(Selector::Name("tap"))?;
        let nsdm=ctx.layers().get(Selector::Name("nsdm"))?;
        let psdm=ctx.layers().get(Selector::Name("psdm"))?;
        let m1=ctx.layers().get(Selector::Metal(1))?;
        let m2=ctx.layers().get(Selector::Metal(2))?;
        let mut terminals: Vec<(&str,i64,i64)>=Vec::new();

        let pid=ctx.mos_db().default_pmos().unwrap().id();
        let nid=ctx.mos_db().default_nmos().unwrap().id();

        for (name,x) in [
            ("OUTPUT_INV",0_i64),
            ("RESTORE_INV0",16_000),
            ("RESTORE_INV1",32_000),
        ] {
            let mut inv=ctx.instantiate::<CometInv>(&self.params)?;
            inv.set_loc(Point::new(x,0));
            for pin in ["a","y","vdd","vss"] {
                let r=inv.port(pin)?.largest_rect(li)?;
                println!("SENSE_PORT={name}.{pin} LI_RECT={r:?}");
            }
            
            let (input,output)=match name {
                "OUTPUT_INV"=>("sense","raw"),
                "RESTORE_INV0"=>("raw","buffered_b"),
                _=>("buffered_b","dout"),
            };
            for (pin,net) in [
                ("a",input),("y",output),("vdd","vdd"),("vss","vss"),
            ] {
                let r=inv.port(pin)?.largest_rect(li)?;
                let px=(match pin {
                    "y"=>r.p0.x+300,
                    "vss"=>r.p1.x-300,
                    _=>(r.p0.x+r.p1.x)/2,
                }).div_euclid(5)*5;
                let py=((r.p0.y+r.p1.y)/2).div_euclid(5)*5;
                terminals.push((net,px,py));
            }
            ctx.draw_ref(&inv)?;

        }

        for (name,x,is_pmos,w,l) in [
            ("NLEFT",48_000_i64,false,self.params.nwidth,self.params.length),
            ("NRIGHT",60_000,false,self.params.nwidth,self.params.length),
            ("NTAIL",72_000,false,420,600),
            ("PMIRROR_REF",84_000,true,self.params.pwidth,self.params.length),
            ("PMIRROR_OUT",96_000,true,self.params.pwidth,self.params.length),
            ("PRESET",108_000,true,self.params.pwidth,self.params.length),
        ] {
            let params=LayoutMosParams {
                devices:vec![MosParams {
                    w,l,m:1,nf:1,id:if is_pmos {pid} else {nid},
                }],
                skip_sd_metal:vec![vec![]],
                deep_nwell:false,
                contact_strategy:GateContactStrategy::SingleSide,
            };
            let mut mos=ctx.instantiate::<LayoutMos>(&params)?;
            mos.set_loc(Point::new(x,0));
            if is_pmos {
                println!(
                    "SENSE_DEVICE={name} NWELL_RECT={:?}",
                    mos.layer_bbox(nwell).into_rect()
                );
            }
            for pin in ["gate_0","sd_0_0","sd_0_1"] {
                let r=mos.port(pin)?.largest_rect(li)?;
                println!("SENSE_PORT={name}.{pin} LI_RECT={r:?}");
            }
            
            let (gate,drain,source)=match name {
                "NLEFT"=>("br","mirror","tail"),
                "NRIGHT"=>("bl","sense","tail"),
                "NTAIL"=>("sa_en","tail","vss"),
                "PMIRROR_REF"=>("mirror","mirror","vdd"),
                "PMIRROR_OUT"=>("mirror","sense","vdd"),
                _=>("sa_en","sense","vdd"),
            };
            for (pin,net) in [
                ("gate_0",gate),("sd_0_0",drain),("sd_0_1",source),
            ] {
                let r=mos.port(pin)?.largest_rect(li)?;
                let py=((r.p0.y+r.p1.y)/2).div_euclid(5)*5;
                let px=if name=="NTAIL" && pin=="sd_0_1" {
                    let px=x+1_200;
                    ctx.draw_rect(li,Rect::new(
                        Point::new(r.p0.x,py-85),
                        Point::new(px+85,py+85),
                    ));
                    px
                } else {
                    (match pin {
                        "sd_0_0"=>if name=="NTAIL" {
                            (r.p0.x+r.p1.x)/2
                        } else {r.p0.x+200},
                        "sd_0_1"=>r.p1.x-200,
                        _=>(r.p0.x+r.p1.x)/2,
                    }).div_euclid(5)*5
                };
                terminals.push((net,px,py));
                println!("SENSE_TERMINAL={name}.{pin} NET={net} X={px} Y={py}");
            }

            let tx=x+6_000;
            let ty=-1_400;
            let region=Rect::new(
                Point::new(tx-250,ty-250),
                Point::new(tx+250,ty+250),
            );
            let vp=ViaParams::builder()
                .layers(tap,li).geometry(region,region)
                .expand(ViaExpansion::Minimum).build();
            let contact=ctx.instantiate::<Via>(&vp)?;
            let ct=contact.layer_bbox(tap).into_rect();
            let cl=contact.layer_bbox(li).into_rect();
            let tb=Rect::new(
                Point::new(ct.p0.x.min(region.p0.x),ct.p0.y.min(region.p0.y)),
                Point::new(ct.p1.x.max(region.p1.x),ct.p1.y.max(region.p1.y)),
            );
            ctx.draw_rect(tap,tb);
            ctx.draw_rect(if is_pmos {nsdm} else {psdm},tb.expand(130));
            if is_pmos {
                let well=mos.layer_bbox(nwell).into_rect();
                ctx.draw_rect(nwell,Rect::new(
                    Point::new(
                        well.p0.x.min(tb.p0.x-400),
                        well.p0.y.min(tb.p0.y-400),
                    ),
                    Point::new(
                        well.p1.x.max(tb.p1.x+400),
                        well.p1.y.max(tb.p1.y+400),
                    ),
                ));
            }
            let px=((cl.p0.x+cl.p1.x)/2).div_euclid(5)*5;
            let py=((cl.p0.y+cl.p1.y)/2).div_euclid(5)*5;
            terminals.push((if is_pmos {"vdd"} else {"vss"},px,py));
            ctx.draw(contact)?;
            ctx.draw_ref(&mos)?;

        }

        
        for (name,track_y,external) in [
            ("bl",6_000_i64,true),("br",7_600,true),
            ("sa_en",9_200,true),
            ("mirror",10_800,false),("sense",12_400,false),
            ("tail",14_000,false),("raw",15_600,false),
            ("buffered_b",17_200,false),("dout",18_800,true),
            ("vdd",20_400,true),("vss",22_000,true),
        ] {
            let mut xmin=i64::MAX;
            let mut xmax=i64::MIN;
            for &(net,x,y) in &terminals {
                if net!=name {continue;}
                xmin=xmin.min(x);
                xmax=xmax.max(x);
                let landing=Rect::new(
                    Point::new(x-85,y-85),Point::new(x+85,y+85),
                );
                let vp=ViaParams::builder()
                    .layers(li,m1).geometry(landing,landing)
                    .expand(ViaExpansion::Minimum).build();
                let via=ctx.instantiate::<Via>(&vp)?;
                ctx.draw(via)?;
                ctx.draw_rect(m1,Rect::new(
                    Point::new(x-85,y.min(track_y)-85),
                    Point::new(x+85,y.max(track_y)+85),
                ));
                let crossing=Rect::new(
                    Point::new(x-200,track_y-200),
                    Point::new(x+200,track_y+200),
                );
                let vp=ViaParams::builder()
                    .layers(m1,m2).geometry(crossing,crossing)
                    .expand(ViaExpansion::Minimum).build();
                let via=ctx.instantiate::<Via>(&vp)?;
                ctx.draw(via)?;
            }
            let wire=Rect::new(
                Point::new(xmin-300,track_y-200),
                Point::new(xmax+300,track_y+200),
            );
            ctx.draw_rect(m2,wire);
            if external {
                let mut port=CellPort::new(name);
                port.add(m2,subgeom::Shape::Rect(wire));
                ctx.add_port(port)?;
            }
            println!("SENSE_ROUTED_NET={name} METAL2_Y_NM={track_y}");
        }
        ctx.flatten();

        Ok(())
    }
}



struct CometRam2x1 {
    params: PrimitiveGateParams,
}

impl Component for CometRam2x1 {
    type Params = PrimitiveGateParams;

    fn new(params: &Self::Params, _: &SubstrateCtx) -> Result<Self> {
        Ok(Self { params: *params })
    }

    fn name(&self) -> ArcStr {
        "comet_ram_2x1".into()
    }

    fn schematic(&self, ctx: &mut SchematicCtx) -> Result<()> {
        let vdd = ctx.port("vdd", Direction::InOut);
        let vss = ctx.port("vss", Direction::InOut);
        let a = ctx.port("a", Direction::Input);
        let en = ctx.port("en", Direction::Input);
        let din = ctx.port("din", Direction::Input);
        let we = ctx.port("we", Direction::Input);
        let pc_b = ctx.port("pc_b", Direction::Input);
        let sa_en = ctx.port("sa_en", Direction::Input);
        let dout = ctx.port("dout", Direction::Output);
        let bl = ctx.signal("bl");
        let br = ctx.signal("br");

        let mut core =
            ctx.instantiate::<CometDecodedArray>(&(self.params, 40_000))?;
        core.connect_all([
            ("vdd", &vdd), ("vss", &vss),
            ("a", &a), ("en", &en), ("bl", &bl), ("br", &br),
        ]);
        core.set_name("CORE");
        ctx.add_instance(core);

        let mut pre_params = self.params;
        pre_params.pwidth = 800;
        let mut pre = ctx.instantiate::<CometPrecharge>(&pre_params)?;
        pre.connect_all([
            ("vdd", &vdd), ("bl", &bl), ("br", &br), ("pc_b", &pc_b),
        ]);
        pre.set_name("PRE");
        ctx.add_instance(pre);

        let mut driver =
            ctx.instantiate::<CometWriteDriver>(&self.params)?;
        driver.connect_all([
            ("vdd", &vdd), ("vss", &vss),
            ("din", &din), ("we", &we), ("bl", &bl), ("br", &br),
        ]);
        driver.set_name("DRV");
        ctx.add_instance(driver);

        let mut sense = ctx.instantiate::<CometSenseAmp>(&self.params)?;
        sense.connect_all([
            ("vdd", &vdd), ("vss", &vss),
            ("bl", &bl), ("br", &br),
            ("sa_en", &sa_en), ("dout", &dout),
        ]);
        sense.set_name("SA");
        ctx.add_instance(sense);
        Ok(())
    }

    fn layout(&self, ctx: &mut LayoutCtx) -> Result<()> {
        let m2 = ctx.layers().get(Selector::Metal(2))?;
        let m3 = ctx.layers().get(Selector::Metal(3))?;

        let mut core =
            ctx.instantiate::<CometDecodedArray>(&(self.params, 40_000))?;
        let mut pre_params = self.params;
        pre_params.pwidth = 800;
        let mut pre = ctx.instantiate::<CometPrecharge>(&pre_params)?;
        let mut driver =
            ctx.instantiate::<CometWriteDriver>(&self.params)?;
        let mut sense = ctx.instantiate::<CometSenseAmp>(&self.params)?;

        core.set_loc(Point::new(0, 0));
        pre.set_loc(Point::new(0, -60_000));
        driver.set_loc(Point::new(0, -120_000));
        sense.set_loc(Point::new(0, -180_000));

        ctx.draw_ref(&core)?;
        ctx.draw_ref(&pre)?;
        ctx.draw_ref(&driver)?;
        ctx.draw_ref(&sense)?;

        for name in ["a", "en"] {
            let r = core.port(name)?.largest_rect(m2)?;
            ctx.add_port(CellPort::with_shape(name, m2, r))?;
        }
        for name in ["din", "we"] {
            let r = driver.port(name)?.largest_rect(m2)?;
            ctx.add_port(CellPort::with_shape(name, m2, r))?;
        }
        let r = pre.port("pc_b")?.largest_rect(m2)?;
        ctx.add_port(CellPort::with_shape("pc_b", m2, r))?;
        for name in ["sa_en", "dout"] {
            let r = sense.port(name)?.largest_rect(m2)?;
            ctx.add_port(CellPort::with_shape(name, m2, r))?;
        }

        for (name, bus_x, core_y) in [
            ("bl", 160_000_i64, 40_000_i64),
            ("br", 162_000, 42_000),
            ("vdd", 164_000, 44_000),
            ("vss", 166_000, 46_000),
        ] {
            let mut terminals = Vec::new();
            let r = core.port(name)?.largest_rect(m2)?;
            // The core exposes these nets on vertical METAL2 rails.
            assert!(r.p0.y + 400 <= core_y && core_y <= r.p1.y - 400,
                    "core port does not cover routing point: {}", name);
            let x = ((r.p0.x + r.p1.x) / 2).div_euclid(5) * 5;
            terminals.push((x, core_y));

            if name != "vss" {
                let r = pre.port(name)?.largest_rect(m2)?;
                terminals.push((
                    ((r.p0.x + r.p1.x) / 2).div_euclid(5) * 5,
                    ((r.p0.y + r.p1.y) / 2).div_euclid(5) * 5,
                ));
            }
            for r in [
                driver.port(name)?.largest_rect(m2)?,
                sense.port(name)?.largest_rect(m2)?,
            ] {
                terminals.push((
                    ((r.p0.x + r.p1.x) / 2).div_euclid(5) * 5,
                    ((r.p0.y + r.p1.y) / 2).div_euclid(5) * 5,
                ));
            }

            let mut ymin = i64::MAX;
            let mut ymax = i64::MIN;
            for (x, y) in terminals {
                ymin = ymin.min(y);
                ymax = ymax.max(y);

                let landing = Rect::new(
                    Point::new(x-200, y-200),
                    Point::new(x+200, y+200),
                );
                let vp = ViaParams::builder()
                    .layers(m2, m3).geometry(landing, landing)
                    .expand(ViaExpansion::Minimum).build();
                let via = ctx.instantiate::<Via>(&vp)?;
                ctx.draw(via)?;

                ctx.draw_rect(m3, Rect::new(
                    Point::new(x.min(bus_x)-400, y-400),
                    Point::new(x.max(bus_x)+400, y+400),
                ));
                let crossing = Rect::new(
                    Point::new(bus_x-400, y-400),
                    Point::new(bus_x+400, y+400),
                );
                let vp = ViaParams::builder()
                    .layers(m2, m3).geometry(crossing, crossing)
                    .expand(ViaExpansion::Minimum).build();
                let via = ctx.instantiate::<Via>(&vp)?;
                ctx.draw(via)?;
                println!("RAM_TERMINAL={name} X={x} Y={y}");
            }

            let bus = Rect::new(
                Point::new(bus_x-500, ymin-500),
                Point::new(bus_x+500, ymax+500),
            );
            ctx.draw_rect(m2, bus);
            if name == "vdd" || name == "vss" {
                ctx.add_port(CellPort::with_shape(name, m2, bus))?;
            }
            println!("RAM_SHARED_NET={name} METAL2_X_NM={bus_x}");
        }

        ctx.flatten();
        Ok(())
    }
}

fn main() -> std::result::Result<(), Box<dyn std::error::Error>> {
    println!("=== COMETRAM-R8 / 53T 전체 통합 : 초기화 ===");
    let root = PathBuf::from(std::env::var("COMETRAM_PDK_ROOT")?);
    if !root.is_dir() {
        return Err(format!("PDK root missing: {}", root.display()).into());
    }
    let cfg = SubstrateConfig::builder()
        .netlister(SpiceNetlister::new())
        .pdk(Sky130OpenPdk::new(&PdkParams { pdk_root: root })?)
        .build();
    let ctx = SubstrateCtx::from_config(cfg)?;
    let params = Inv::dec_params();

    ctx.write_schematic_to_file::<CometRam2x1>(
        &params, "output/comet_ram_2x1.spice",
    )?;
    println!("RAM_SCHEMATIC=COMPLETED");
    println!("EXPECTED_MOS_COUNT=53");

    ctx.write_layout::<CometRam2x1>(
        &params, "output/comet_ram_2x1.gds",
    )?;
    println!("RAM_LAYOUT_GENERATION=COMPLETED");
    println!("RAM_DRC_LVS=NOT_RUN");
    println!("RAM_EXTRACTED_SIMULATION=NOT_RUN");
    println!("RAM_PARASITIC_RC_VALIDATION=NOT_RUN");
    Ok(())
}
