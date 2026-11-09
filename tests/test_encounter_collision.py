"""Independent collision cases: an entire body crossing inside one solver step."""
import unittest
import numpy as np
from solver.nbody_propagation import propagate_conic


class EncounterCollisionTests(unittest.TestCase):
    def crossing(self, offset=0., kinematic=True, radius=10., velocity=0.):
        def surface(t, y):
            return float(np.linalg.norm(y[:3] - [1e8+123456.7+velocity*t, offset, 0]))-radius
        if kinematic:
            def closest(t, y):
                return float(np.dot(y[:3]-[1e8+123456.7+velocity*t, offset, 0], y[3:]-[velocity,0,0]))
            surface.closest_approach=closest
        return propagate_conic(([1e8,0,0],[1000,0,0]),1000,1,sample_count=2,
                               other_collisions=[('body',surface)])

    def test_fast_crossing_detects_first_surface_not_exit(self):
        for kinematic in (True, False):
            with self.subTest(kinematic=kinematic):
                result=self.crossing(kinematic=kinematic)
                self.assertTrue(result['collision'])
                self.assertAlmostEqual(result['durationSeconds'],123.4467,places=5)
                self.assertEqual(result['events'][-1]['type'],'collision:body')

    def test_near_miss_is_not_a_collision(self):
        self.assertFalse(self.crossing(offset=10.1)['collision'])

    def test_moving_neptune_sized_body_is_detected(self):
        result=self.crossing(radius=24622.,velocity=5.)
        self.assertTrue(result['collision'])
        self.assertAlmostEqual(result['durationSeconds'],(123456.7-24622)/995,places=5)

    def test_initially_inside_body_is_rejected_at_zero(self):
        result=propagate_conic(([7000,0,0],[0,7,0]),100,398600,collision_radius_km=7100)
        self.assertTrue(result['collision'])
        self.assertEqual(result['durationSeconds'],0)
